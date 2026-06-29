#!/usr/bin/env python
"""CLI entrypoint for the job-application pipeline.

  python run.py                 # ★ do everything: fetch -> tailor + score -> open review queue
  python run.py init            # create config.yaml from the example
  python run.py serve           # just the review web server (no fetch)
  python run.py fetch           # just fetch from job-board APIs -> tailor new jobs
  python run.py process         # tailor + render every NEW job (manual batch)
  python run.py capture -f job.json   # add a job from a JSON file (paste fallback)
  python run.py list            # show all tracked jobs
  python run.py linkedin-login / scrape   # optional, DISCOURAGED LinkedIn tools
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys

from jobapply.config import REPO_ROOT, load_config


def cmd_init(_args) -> None:
    src = REPO_ROOT / "config.example.yaml"
    dst = REPO_ROOT / "config.yaml"
    if dst.exists():
        print(f"config.yaml already exists at {dst}")
        return
    shutil.copy(src, dst)
    print(f"Created {dst}\nEdit it to fill in your job criteria, then `python run.py serve`.")


def cmd_serve(_args) -> None:
    import uvicorn
    from jobapply.web import create_app
    cfg = load_config()
    app = create_app(cfg)
    print(f"Dashboard:   http://{cfg.host}:{cfg.port}/")
    print(f"Bookmarklet: http://{cfg.host}:{cfg.port}/bookmarklet")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


def cmd_auto(_args) -> None:
    """Default (no subcommand): scrape -> tailor + score -> open the review dashboard."""
    import threading
    import webbrowser

    import uvicorn
    from jobapply.models import Status
    from jobapply.pipeline import process_new
    from jobapply.store import Store
    from jobapply.tracker import Tracker
    from jobapply.web import create_app

    cfg = load_config()
    store, tracker = Store(cfg.db_path), Tracker(cfg.tracker_xlsx)

    if cfg.jobapis_enabled:
        print("→ Fetching jobs from job-board APIs…")
        try:
            from jobapply.sources.jobapis import fetch_all
            new = fetch_all(cfg, store)
            print(f"  +{len(new)} new job(s) total")
            for j in new:
                tracker.upsert(store.get(j.id))
        except Exception as exc:  # noqa: BLE001 - keep going to review what we have
            print(f"  sourcing skipped: {exc}")
    else:
        print("→ No job sources enabled — skipping fetch.")

    pending = store.by_status(Status.NEW)
    if pending:
        print(f"→ Tailoring + scoring {len(pending)} new job(s)…")
        for j in process_new(cfg, store, tracker):
            print(f"  [{j.status.value:14}] fit={j.fit_score}  {j.company} — {j.title}")
    store.close()

    url = f"http://{cfg.host}:{cfg.port}/"
    print(f"\n→ Review queue ready: {url}")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")


def cmd_process(_args) -> None:
    from jobapply.pipeline import process_new
    from jobapply.store import Store
    from jobapply.tracker import Tracker
    cfg = load_config()
    store, tracker = Store(cfg.db_path), Tracker(cfg.tracker_xlsx)
    jobs = process_new(cfg, store, tracker)
    if not jobs:
        print("No NEW jobs to process.")
    for j in jobs:
        print(f"[{j.status.value:14}] fit={j.fit_score}  {j.company} — {j.title}")
    store.close()


def cmd_capture(args) -> None:
    from jobapply.models import Job
    from jobapply.store import Store
    from jobapply.tracker import Tracker
    cfg = load_config()
    raw = json.load(open(args.file, encoding="utf-8")) if args.file else json.load(sys.stdin)
    job = Job(**{k: raw.get(k, "") for k in
                 ("title", "company", "location", "url", "source", "description")})
    store, tracker = Store(cfg.db_path), Tracker(cfg.tracker_xlsx)
    is_new = store.upsert(job)
    tracker.upsert(store.get(job.id))
    print(("Captured " if is_new else "Duplicate ") + f"{job.id}  {job.title}")
    store.close()


def cmd_fetch(args) -> None:
    from jobapply.pipeline import process_new
    from jobapply.sources.jobapis import fetch_all
    from jobapply.store import Store
    from jobapply.tracker import Tracker
    cfg = load_config()
    if not cfg.jobapis_enabled:
        print("No jobapis.enabled providers in config.yaml.")
        return
    store, tracker = Store(cfg.db_path), Tracker(cfg.tracker_xlsx)
    new = fetch_all(cfg, store)
    print(f"Fetched {len(new)} new job(s).")
    for j in new:
        tracker.upsert(store.get(j.id))
    if not args.no_process and new:
        print("Tailoring + scoring…")
        for j in process_new(cfg, store, tracker):
            print(f"  [{j.status.value:14}] fit={j.fit_score}  {j.company} — {j.title}")
    store.close()


def cmd_linkedin_login(_args) -> None:
    from jobapply.sources.linkedin import login
    login(load_config())


def cmd_scrape(args) -> None:
    from jobapply.pipeline import process_new
    from jobapply.sources.linkedin import scrape
    from jobapply.store import Store
    from jobapply.tracker import Tracker
    cfg = load_config()
    if not cfg.linkedin_has_searches:
        print("Nothing to search — add criteria.titles, linkedin.keywords, or linkedin.searches.")
        return
    store, tracker = Store(cfg.db_path), Tracker(cfg.tracker_xlsx)
    new = scrape(cfg, store)
    print(f"Scraped {len(new)} new job(s).")
    for j in new:
        tracker.upsert(store.get(j.id))
    if not args.no_process and new:
        print("Tailoring + rendering…")
        for j in process_new(cfg, store, tracker):
            print(f"  [{j.status.value:14}] fit={j.fit_score}  {j.company} — {j.title}")
    store.close()


def cmd_list(_args) -> None:
    from jobapply.store import Store
    cfg = load_config()
    store = Store(cfg.db_path)
    for j in store.all():
        print(f"{j.id}  [{j.status.value:14}] fit={j.fit_score}  {j.company} — {j.title}")
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Human-in-the-loop job application pipeline. "
                    "Run with no command to scrape, tailor, score, and open the review queue.")
    parser.set_defaults(func=cmd_auto)  # `python run.py` with no subcommand
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("serve").set_defaults(func=cmd_serve)
    sub.add_parser("process").set_defaults(func=cmd_process)
    cap = sub.add_parser("capture")
    cap.add_argument("-f", "--file", help="JSON file with job fields")
    cap.set_defaults(func=cmd_capture)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--no-process", action="store_true",
                       help="only ingest jobs; skip tailoring/render")
    fetch.set_defaults(func=cmd_fetch)
    sub.add_parser("linkedin-login").set_defaults(func=cmd_linkedin_login)
    scr = sub.add_parser("scrape")
    scr.add_argument("--no-process", action="store_true",
                     help="only ingest jobs; skip tailoring/render")
    scr.set_defaults(func=cmd_scrape)
    sub.add_parser("list").set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
