"""LinkedIn job-search scraper (source adapter).

Drives a persistent, logged-in browser profile through your saved-search URLs and
ingests new postings into the pipeline. READ-ONLY: it collects job listings, it
never applies. Intended to run from a BURNER account so any anti-bot action falls
on the throwaway, not your real account (which only ever submits applications).

It scrapes from the search results' detail PANE (click a card -> read the side
panel), not the standalone /jobs/view/ page — the standalone page is served with
obfuscated CSS classes, while the search pane keeps stable semantic selectors.

Usage:
  1. `python run.py linkedin-login`  — one-time: a real browser opens, you sign in
     to the burner account (handle any 2FA/CAPTCHA), session is saved to the profile.
  2. `python run.py scrape`          — reuses that session, walks your searches,
     tailors + renders each new job. Schedule this overnight.

If a run collects 0 jobs or empty descriptions, set `linkedin.debug: true` to dump
screenshots to data/debug/ for tuning.
"""
from __future__ import annotations

import random
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from ..config import Config
from ..models import Job
from ..store import Store

LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"
SEARCH_URL = "https://www.linkedin.com/jobs/search/?"
JOB_VIEW = "https://www.linkedin.com/jobs/view/{id}/"
_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")
_LOC_SPLIT = re.compile(r"[·•\n]")  # middle-dot / bullet / newline

# LinkedIn filter-code maps for building search URLs from criteria.
_WORK_TYPE = {"remote": "2", "hybrid": "3", "on-site": "1", "onsite": "1", "on site": "1"}
_EXPERIENCE = {  # LinkedIn f_E codes
    "intern": "1", "internship": "1", "entry": "2", "entry-level": "2", "junior": "2",
    "associate": "3", "mid": "4", "mid-senior": "4", "midsenior": "4", "medior": "4",
    "senior": "4", "lead": "4", "staff": "4", "principal": "5", "director": "5",
    "executive": "6", "exec": "6",
}
_DATE_POSTED = {"24h": "r86400", "day": "r86400", "3d": "r259200", "week": "r604800",
                "7d": "r604800", "month": "r2592000", "30d": "r2592000"}


def _codes(values, table) -> str:
    out: list[str] = []
    for v in values:
        c = table.get(str(v).strip().lower())
        if c and c not in out:
            out.append(c)
    return ",".join(out)


def _build_searches(cfg: Config) -> list[str]:
    """Construct LinkedIn job-search URLs from the user's criteria + filter config."""
    crit = cfg.criteria
    keywords = cfg.linkedin_keywords or crit.get("titles") or []
    if not keywords:
        raise RuntimeError(
            "Nothing to search: add criteria.titles, linkedin.keywords, or linkedin.searches."
        )
    f_wt = _codes(crit.get("arrangement", []), _WORK_TYPE)
    f_e = _codes(re.split(r"[,/;]| and ", str(crit.get("seniority", ""))), _EXPERIENCE)
    f_tpr = _DATE_POSTED.get(str(cfg.linkedin_date_posted).lower(), "r86400")

    urls = []
    for kw in keywords:
        params = {"keywords": kw, "sortBy": "DD", "f_TPR": f_tpr}
        if cfg.linkedin_geo_id:
            params["geoId"] = cfg.linkedin_geo_id
        if f_wt:
            params["f_WT"] = f_wt
        if f_e:
            params["f_E"] = f_e
        urls.append(SEARCH_URL + urlencode(params))
    return urls


def resolve_searches(cfg: Config) -> list[str]:
    """Explicit `linkedin.searches` win; otherwise build them from criteria."""
    return cfg.linkedin_searches or _build_searches(cfg)

# Result-card link selectors (the classic search UI keeps semantic classes).
_CARD_LINK = ["a.job-card-container__link", "a.job-card-list__title--link"]

# Detail-pane selectors (first non-empty wins).
_TITLE = [".job-details-jobs-unified-top-card__job-title", ".t-24",
          ".jobs-unified-top-card__job-title"]
_COMPANY = [".job-details-jobs-unified-top-card__company-name a",
            ".job-details-jobs-unified-top-card__company-name",
            ".jobs-unified-top-card__company-name"]
_LOCATION = [".job-details-jobs-unified-top-card__primary-description-container",
             ".job-details-jobs-unified-top-card__tertiary-description-container"]
_DESC = ["#job-details", ".jobs-description__content", ".jobs-box__html-content",
         ".jobs-description-content__text"]


def _sleep(cfg: Config) -> None:
    """Human-like pause between actions (also the main anti-detection lever)."""
    time.sleep(random.uniform(cfg.linkedin_min_delay, cfg.linkedin_max_delay))


def _blocked(url: str) -> bool:
    return any(x in url for x in ("/login", "/authwall", "/checkpoint", "/uas/"))


def _wait_if_blocked(page, cfg: Config, wait_secs: int = 180) -> None:
    """If LinkedIn shows a login/checkpoint/CAPTCHA, pause (visible browser) so the
    user can solve it by hand, then continue once it clears."""
    if not _blocked(page.url):
        return
    if cfg.linkedin_headless:
        raise RuntimeError(
            "Hit a LinkedIn checkpoint while running headless — you can't solve it. "
            "Set linkedin.headless: false in config.yaml and re-run."
        )
    print("\n⚠  LinkedIn is showing a login/checkpoint/CAPTCHA.")
    print(f"   Solve it in the OPEN BROWSER WINDOW. Waiting up to {wait_secs}s…")
    deadline = time.time() + wait_secs
    while _blocked(page.url) and time.time() < deadline:
        time.sleep(3)
    if _blocked(page.url):
        raise RuntimeError("Still blocked after waiting — re-run once the challenge is cleared.")
    print("   Challenge cleared — continuing.\n")


def _text(page, selectors: list[str]) -> str:
    for sel in selectors:
        el = page.query_selector(sel)
        if el:
            t = (el.inner_text() or "").strip()
            if t:
                return t
    return ""


def _with_start(url: str, start: int) -> str:
    """Set the `start` pagination offset on a LinkedIn search URL."""
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query))
    q["start"] = str(start)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


def _new_context(p, cfg: Config, headless: bool):
    """Launch a persistent browser that looks like a normal one (real Chrome +
    automation fingerprints stripped) so LinkedIn's CAPTCHA actually renders."""
    cfg.linkedin_profile_dir.mkdir(parents=True, exist_ok=True)
    opts = dict(
        user_data_dir=str(cfg.linkedin_profile_dir),
        headless=headless,
        viewport={"width": 1366, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
        ignore_default_args=["--enable-automation"],
    )
    try:
        ctx = p.chromium.launch_persistent_context(
            channel=cfg.linkedin_browser_channel or None, **opts)
    except Exception:  # noqa: BLE001 - real Chrome not installed → bundled Chromium
        ctx = p.chromium.launch_persistent_context(**opts)
    ctx.add_init_script(_STEALTH_JS)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    return ctx, page


def login(cfg: Config) -> None:
    """One-time interactive login. Opens a real browser; you sign in by hand."""
    with sync_playwright() as p:
        ctx, page = _new_context(p, cfg, headless=False)
        page.goto(LOGIN_URL)
        print("\nA browser window opened.")
        print("→ Log in to your BURNER LinkedIn account and reach your feed/home.")
        print("→ Complete any 2FA or CAPTCHA challenge.")
        input("When you're fully logged in, press Enter here to save the session... ")
        ctx.close()
    print(f"Session saved to {cfg.linkedin_profile_dir}")


def _collect_job_ids(page, cfg: Config) -> list[str]:
    """Scroll the results list, harvesting unique /jobs/view/<id> ids in order."""
    ids: list[str] = []
    seen: set[str] = set()
    for _ in range(8):
        hrefs = page.eval_on_selector_all(
            "a[href*='/jobs/view/']", "els => els.map(e => e.getAttribute('href'))"
        ) or []
        for h in hrefs:
            m = _JOB_ID_RE.search(h or "")
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                ids.append(m.group(1))
        if len(ids) >= cfg.linkedin_max_jobs:
            break
        page.mouse.wheel(0, 2200)  # trigger lazy-loading of more cards
        _sleep(cfg)
    return ids[: cfg.linkedin_max_jobs]


def _read_pane(page, cfg: Config) -> tuple[str, str, str, str] | None:
    """Read the currently-open job from the search detail pane."""
    try:
        page.wait_for_selector(",".join(_DESC), timeout=8000)
    except PWTimeout:
        return None
    title = _text(page, _TITLE)
    company = _text(page, _COMPANY)
    loc_raw = _text(page, _LOCATION)
    location = _LOC_SPLIT.split(loc_raw)[0].strip() if loc_raw else ""
    desc = _text(page, _DESC)
    if not desc:
        return None
    return title, company, location, desc


def scrape(cfg: Config, store: Store) -> list[Job]:
    """Walk every configured search and ingest new jobs. Returns the new ones."""
    searches = resolve_searches(cfg)
    new_jobs: list[Job] = []
    with sync_playwright() as p:
        ctx, page = _new_context(p, cfg, headless=cfg.linkedin_headless)
        try:
            page.goto(FEED_URL, wait_until="domcontentloaded")
            _wait_if_blocked(page, cfg)

            for search_url in searches:
                collected = 0
                start = 0
                while collected < cfg.linkedin_max_jobs:
                    page.goto(_with_start(search_url, start), wait_until="domcontentloaded")
                    _wait_if_blocked(page, cfg)
                    _sleep(cfg)
                    ids = _collect_job_ids(page, cfg)
                    if not ids:
                        break
                    for jid in ids:
                        if collected >= cfg.linkedin_max_jobs:
                            break
                        canonical = JOB_VIEW.format(id=jid)
                        if store.get(Job.make_id(canonical)) is not None:
                            continue  # already seen on a previous run
                        link = page.query_selector(f"a[href*='/jobs/view/{jid}']")
                        if link is None:
                            continue
                        try:
                            link.scroll_into_view_if_needed(timeout=4000)
                            link.click(timeout=4000)
                        except Exception:
                            continue
                        _sleep(cfg)
                        collected += 1
                        fields = _read_pane(page, cfg)
                        if fields is None:
                            if cfg.linkedin_debug:
                                cfg.debug_dir.mkdir(parents=True, exist_ok=True)
                                page.screenshot(path=str(cfg.debug_dir / f"pane_{jid}.png"),
                                                full_page=True)
                            continue
                        title, company, location, desc = fields
                        job = Job(title=title, company=company, location=location,
                                  url=canonical, source="linkedin", description=desc)
                        if store.upsert(job):
                            new_jobs.append(job)
                    if len(ids) < 25:  # last page of this search
                        break
                    start += 25
        finally:
            ctx.close()
    return new_jobs
