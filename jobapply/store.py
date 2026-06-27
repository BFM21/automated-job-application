"""SQLite-backed job store. Handles dedupe (one row per job id) and state
transitions across the pipeline."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .models import Job, Status, _now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                 TEXT PRIMARY KEY,
    title              TEXT,
    company            TEXT,
    location           TEXT,
    url                TEXT,
    source             TEXT,
    description        TEXT,
    status             TEXT,
    fit_score          INTEGER,
    fit_reasons        TEXT,
    tailored_data_path TEXT,
    pdf_path           TEXT,
    error              TEXT,
    captured_at        TEXT,
    updated_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

_COLUMNS = [
    "id", "title", "company", "location", "url", "source", "description",
    "status", "fit_score", "fit_reasons", "tailored_data_path", "pdf_path",
    "error", "captured_at", "updated_at",
]


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        # check_same_thread=False + a lock: the web server touches the store from
        # request threads and Playwright/Claude background worker threads.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── writes ──────────────────────────────────────────────────────────────
    def upsert(self, job: Job) -> bool:
        """Insert a job, or no-op if its id already exists. Returns True if new."""
        job.updated_at = _now()
        row = job.to_row()
        placeholders = ", ".join("?" for _ in _COLUMNS)
        cols = ", ".join(_COLUMNS)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT OR IGNORE INTO jobs ({cols}) VALUES ({placeholders})",
                [row[c] for c in _COLUMNS],
            )
            self._conn.commit()
        return cur.rowcount > 0

    def save(self, job: Job) -> None:
        """Full overwrite of an existing job (used for state transitions)."""
        job.updated_at = _now()
        row = job.to_row()
        assignments = ", ".join(f"{c} = ?" for c in _COLUMNS if c != "id")
        with self._lock:
            self._conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                [row[c] for c in _COLUMNS if c != "id"] + [row["id"]],
            )
            self._conn.commit()

    def set_status(self, job_id: str, status: Status, **fields) -> None:
        job = self.get(job_id)
        if not job:
            return
        job.status = status
        for k, v in fields.items():
            setattr(job, k, v)
        self.save(job)

    # ── reads ───────────────────────────────────────────────────────────────
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
        return Job.from_row(dict(row)) if row else None

    def by_status(self, *statuses: Status) -> list[Job]:
        marks = ", ".join("?" for _ in statuses)
        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({marks}) "
                "ORDER BY fit_score DESC NULLS LAST, captured_at DESC",
                [s.value for s in statuses],
            )
            rows = cur.fetchall()
        return [Job.from_row(dict(r)) for r in rows]

    def all(self) -> list[Job]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs ORDER BY captured_at DESC")
            rows = cur.fetchall()
        return [Job.from_row(dict(r)) for r in rows]

    def close(self) -> None:
        self._conn.close()
