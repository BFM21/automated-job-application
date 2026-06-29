"""Legal job-board API sources — no login, no scraping, no ban risk.

Each provider exposes a public JSON endpoint with full job descriptions. We
keyword-filter on the title to the candidate's focus and ingest into the same
pipeline as everything else. Add/remove providers in `jobapis.enabled`.
"""
from __future__ import annotations

import html as _html
import json
import re
import urllib.request

from ..config import Config
from ..models import Job
from ..store import Store

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _strip_html(raw: str) -> str:
    """Turn an HTML job description into readable plain text."""
    if not raw:
        return ""
    raw = re.sub(r"(?i)<\s*(br|/p|/div|/h[1-6]|/li)\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)<li[^>]*>", "• ", raw)
    text = _html.unescape(re.sub(r"<[^>]+>", "", raw))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _matches(title: str, tags, keywords: list[str]) -> bool:
    """Match keywords against the job TITLE. (Tag matching pulls far too much noise
    on general boards, so it's intentionally title-only; the fit-scorer ranks the rest.)"""
    if not keywords:
        return True
    t = title.lower()
    return any(k.lower() in t for k in keywords)


# ── providers (each returns a list of Job) ───────────────────────────────────
def remotive(keywords: list[str], limit: int) -> list[Job]:
    # software-dev category keeps it to engineering roles, not all remote jobs.
    data = _get_json("https://remotive.com/api/remote-jobs?category=software-dev&limit=100")
    jobs: list[Job] = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        if not _matches(title, j.get("tags"), keywords):
            continue
        jobs.append(Job(
            title=title, company=j.get("company_name", ""),
            location=j.get("candidate_required_location") or "Remote",
            url=j.get("url", ""), source="remotive",
            description=_strip_html(j.get("description", "")),
        ))
        if len(jobs) >= limit:
            break
    return jobs


def arbeitnow(keywords: list[str], limit: int) -> list[Job]:
    data = _get_json("https://www.arbeitnow.com/api/job-board-api")
    jobs: list[Job] = []
    for j in data.get("data", []):
        title = j.get("title", "")
        if not _matches(title, j.get("tags"), keywords):
            continue
        loc = j.get("location", "") or ("Remote" if j.get("remote") else "")
        if j.get("remote") and "remote" not in loc.lower():
            loc = (loc + " · Remote").strip(" ·")
        jobs.append(Job(
            title=title, company=j.get("company_name", ""), location=loc,
            url=j.get("url", ""), source="arbeitnow",
            description=_strip_html(j.get("description", "")),
        ))
        if len(jobs) >= limit:
            break
    return jobs


def remoteok(keywords: list[str], limit: int) -> list[Job]:
    data = _get_json("https://remoteok.com/api")
    jobs: list[Job] = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first array element is a legal/usage notice
        title = j.get("position", "")
        if not _matches(title, j.get("tags"), keywords):
            continue
        jobs.append(Job(
            title=title, company=j.get("company", ""),
            location=j.get("location") or "Remote", url=j.get("url", ""),
            source="remoteok", description=_strip_html(j.get("description", "")),
        ))
        if len(jobs) >= limit:
            break
    return jobs


_PROVIDERS = {"remotive": remotive, "arbeitnow": arbeitnow, "remoteok": remoteok}


def fetch_all(cfg: Config, store: Store) -> list[Job]:
    """Run every enabled provider, keyword-filter, dedupe, and ingest new jobs."""
    new_jobs: list[Job] = []
    for name in cfg.jobapis_enabled:
        try:
            if name == "linkedin":
                from . import linkedin_guest  # server-side guest API; needs cfg for filters
                found = linkedin_guest.fetch(cfg)
            else:
                fn = _PROVIDERS.get(name)
                if fn is None:
                    print(f"  {name}: unknown provider — skipping")
                    continue
                found = fn(cfg.jobapis_keywords, cfg.jobapis_max_per_source)
        except Exception as exc:  # noqa: BLE001 - one source down shouldn't kill the run
            print(f"  {name}: error — {exc}")
            continue
        added = 0
        for job in found:
            if job.description and store.upsert(job):
                new_jobs.append(job)
                added += 1
        print(f"  {name}: {added} new (of {len(found)} matched)")
    return new_jobs
