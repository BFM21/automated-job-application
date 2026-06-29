#!/usr/bin/env python
"""Wipe runtime data for a clean slate.

Removes the jobs database, tailored resume JSON/PDFs, the Excel tracker, captured
jobs, and debug screenshots. KEEPS your LinkedIn login session by default so you
don't have to sign in again.

  python clear.py          # clear job data, keep LinkedIn session (asks first)
  python clear.py -y       # skip the confirmation prompt
  python clear.py --all    # ALSO clear the LinkedIn session (forces re-login)
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from jobapply.config import load_config


def _rm_file(p: Path) -> bool:
    removed = False
    for path in (p, p.with_suffix(p.suffix + "-wal"), p.with_suffix(p.suffix + "-shm")):
        if path.exists():
            path.unlink()
            removed = True
    return removed


def _rm_dir(p: Path) -> int:
    """Remove a directory and report how many files it held."""
    if not p.exists():
        return 0
    n = sum(1 for _ in p.rglob("*") if _.is_file())
    shutil.rmtree(p)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Wipe runtime data for a clean slate.")
    ap.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    ap.add_argument("--all", action="store_true",
                    help="also clear the LinkedIn session (you'll re-login)")
    args = ap.parse_args()

    cfg = load_config()
    targets = [
        ("jobs database", cfg.db_path, "file"),
        ("Excel tracker", cfg.tracker_xlsx, "file"),
        ("tailored resumes/PDFs", cfg.pdf_dir, "dir"),
        ("captured jobs", cfg.captures_dir, "dir"),
        ("debug screenshots", cfg.debug_dir, "dir"),
    ]
    if args.all:
        targets.append(("LinkedIn session", cfg.linkedin_profile_dir, "dir"))

    print("About to clear:")
    for label, path, _kind in targets:
        exists = path.exists()
        print(f"  {'x' if exists else '-'} {label:24} {path}")
    if not args.all:
        print(f"  (keeping LinkedIn session at {cfg.linkedin_profile_dir})")

    if not args.yes:
        if input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return

    failed = False
    for label, path, kind in targets:
        try:
            if kind == "file":
                print(f"  removed {label}" if _rm_file(path) else f"  (no {label})")
            else:
                n = _rm_dir(path)
                print(f"  removed {label} ({n} files)" if n or path == cfg.linkedin_profile_dir
                      else f"  (no {label})")
        except PermissionError:
            failed = True
            print(f"  ! {label} is in use — stop the server (python run.py serve) and retry")
    if failed:
        print("\nSome items were locked. Close the dashboard / stop the server, then re-run.")
        return

    cfg.ensure_dirs()  # recreate the empty data/resumes/captures dirs
    print("\nClean slate ready.")


if __name__ == "__main__":
    main()
