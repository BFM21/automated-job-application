#!/usr/bin/env python
"""CLI entrypoint for the job-application pipeline.

  python run.py init       # create config.yaml from the example
  python run.py serve      # start the capture + review web server
  python run.py process    # tailor + render every NEW job (manual batch)
  python run.py capture -f job.json   # add a job from a JSON file (paste fallback)
  python run.py list       # show all tracked jobs
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


def cmd_list(_args) -> None:
    from jobapply.store import Store
    cfg = load_config()
    store = Store(cfg.db_path)
    for j in store.all():
        print(f"{j.id}  [{j.status.value:14}] fit={j.fit_score}  {j.company} — {j.title}")
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Human-in-the-loop job application pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("serve").set_defaults(func=cmd_serve)
    sub.add_parser("process").set_defaults(func=cmd_process)
    cap = sub.add_parser("capture")
    cap.add_argument("-f", "--file", help="JSON file with job fields")
    cap.set_defaults(func=cmd_capture)
    sub.add_parser("list").set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
