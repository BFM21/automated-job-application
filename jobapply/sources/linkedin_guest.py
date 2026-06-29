"""LinkedIn via its PUBLIC guest API — server-side, anonymous, no login.

LinkedIn exposes unauthenticated guest endpoints for job search and job detail
(the same ones that power logged-out job pages). We hit them from the pipeline:
no browser, no bookmarklet (LinkedIn's CSP blocks those), no account — so there's
nothing to restrict or ban. Worst case is a transient IP rate-limit, which polite
delays avoid.

Search filters are built from the user's criteria, exactly like the old scraper.
"""
from __future__ import annotations

import html as _html
import random
import re
import time
import urllib.parse
import urllib.request

from ..config import Config
from ..models import Job

_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_URN = re.compile(r"urn:li:jobPosting:(\d+)")

_WORK_TYPE = {"remote": "2", "hybrid": "3", "on-site": "1", "onsite": "1", "on site": "1"}
_EXPERIENCE = {"intern": "1", "internship": "1", "entry": "2", "junior": "2",
               "associate": "3", "mid": "4", "mid-senior": "4", "medior": "4",
               "senior": "4", "lead": "4", "staff": "4", "principal": "5",
               "director": "5", "executive": "6"}
_DATE = {"24h": "r86400", "day": "r86400", "3d": "r259200", "week": "r604800",
         "7d": "r604800", "month": "r2592000", "30d": "r2592000"}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _codes(values, table) -> str:
    out = []
    for v in values:
        c = table.get(str(v).strip().lower())
        if c and c not in out:
            out.append(c)
    return ",".join(out)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def _desc(html_str: str) -> str:
    m = re.search(r"show-more-less-html__markup[^>]*>(.*?)</section", html_str, re.S)
    raw = m.group(1) if m else ""
    raw = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)<li[^>]*>", "• ", raw)
    text = _html.unescape(re.sub(r"<[^>]+>", "", raw))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _search_ids(cfg: Config, keyword: str) -> list[str]:
    params = {"keywords": keyword, "start": "0",
              "f_TPR": _DATE.get(str(cfg.linkedin_date_posted).lower(), "r86400")}
    if cfg.linkedin_geo_id:
        params["geoId"] = cfg.linkedin_geo_id
    wt = _codes(cfg.criteria.get("arrangement", []), _WORK_TYPE)
    if wt:
        params["f_WT"] = wt
    fe = _codes(re.split(r"[,/;]| and ", str(cfg.criteria.get("seniority", ""))), _EXPERIENCE)
    if fe:
        params["f_E"] = fe
    html_str = _get(_SEARCH + urllib.parse.urlencode(params))
    seen, ids = set(), []
    for jid in _URN.findall(html_str):
        if jid not in seen:
            seen.add(jid)
            ids.append(jid)
    return ids


def _fetch_detail(jid: str) -> Job | None:
    html_str = _get(_DETAIL.format(id=jid))
    title = _clean((re.search(r"top-card-layout__title[^>]*>(.*?)</h", html_str, re.S)
                    or re.search(r"<h2[^>]*>(.*?)</h2>", html_str, re.S) or [None, ""])[1])
    company = _clean((re.search(r"topcard__org-name-link[^>]*>(.*?)</a>", html_str, re.S)
                      or re.search(r"topcard__flavor[^>]*>(.*?)</span>", html_str, re.S)
                      or [None, ""])[1])
    location = _clean((re.search(r"topcard__flavor--bullet[^>]*>(.*?)</span>", html_str, re.S)
                       or [None, ""])[1])
    desc = _desc(html_str)
    if not desc:
        return None
    return Job(title=title, company=company, location=location,
               url=f"https://www.linkedin.com/jobs/view/{jid}/",
               source="linkedin", description=desc)


def fetch(cfg: Config) -> list[Job]:
    """Run a guest search per keyword, then pull full details for each new job."""
    keywords = cfg.jobapis_keywords or cfg.criteria.get("titles") or []
    cap = cfg.jobapis_max_per_source
    ids: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        try:
            for jid in _search_ids(cfg, kw):
                if jid not in seen:
                    seen.add(jid)
                    ids.append(jid)
        except Exception as exc:  # noqa: BLE001
            print(f"    linkedin search '{kw}' failed: {exc}")
        time.sleep(random.uniform(0.6, 1.4))
        if len(ids) >= cap:
            break
    jobs: list[Job] = []
    for jid in ids[:cap]:
        try:
            job = _fetch_detail(jid)
            if job:
                jobs.append(job)
        except Exception:  # noqa: BLE001 - skip a job that won't load
            pass
        time.sleep(random.uniform(0.5, 1.1))
    return jobs
