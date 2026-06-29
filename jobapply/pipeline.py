"""Glue: take captured jobs from NEW -> PENDING_REVIEW by tailoring then rendering.

For the CLI backend with more than one new job, tailoring is done in a SINGLE
`claude` call (one process, not one per job). Rendering stays per-job (it's a
fast, local headless print). Any job the batch omits falls back to a single call.
"""
from __future__ import annotations

import traceback

from .config import Config
from .enrich import enrich
from .models import Job, Status
from .render import render_pdf
from .store import Store
from .tracker import Tracker


def _render_and_save(job: Job, json_path, fit: int, reasons: str,
                     cfg: Config, store: Store, tracker: Tracker) -> Job:
    """Render the tailored JSON to PDF and persist the job's final state."""
    try:
        job.pdf_path = str(render_pdf(job, json_path, cfg))
        job.fit_score = fit
        job.fit_reasons = reasons
        job.tailored_data_path = str(json_path)
        job.status = Status.PENDING_REVIEW
        job.error = ""
    except Exception as exc:  # noqa: BLE001
        job.status = Status.ERROR
        job.error = f"render: {exc}\n{traceback.format_exc()}"
    store.save(job)
    tracker.upsert(job)
    return job


def process_job(job: Job, cfg: Config, store: Store, tracker: Tracker) -> Job:
    """Tailor + render a single job. Never raises; failures land in ERROR."""
    store.set_status(job.id, Status.PROCESSING)
    try:
        fit, reasons, json_path = enrich(job, cfg)
    except Exception as exc:  # noqa: BLE001
        job.status = Status.ERROR
        job.error = f"{exc}\n{traceback.format_exc()}"
        store.save(job)
        tracker.upsert(job)
        return job
    return _render_and_save(job, json_path, fit, reasons, cfg, store, tracker)


def _process_batch(jobs: list[Job], cfg: Config, store: Store, tracker: Tracker) -> list[Job]:
    """One claude call tailors all jobs; omitted/failed ones fall back per-job."""
    from .enrich import enrich_batch

    for j in jobs:
        store.set_status(j.id, Status.PROCESSING)
    try:
        results = enrich_batch(jobs, cfg)
    except Exception:  # noqa: BLE001 - whole batch failed; do them individually
        results = {}

    done: list[Job] = []
    for job in jobs:
        if job.id in results:
            fit, reasons, json_path = results[job.id]
            done.append(_render_and_save(job, json_path, fit, reasons, cfg, store, tracker))
        else:
            done.append(process_job(job, cfg, store, tracker))
    return done


def process_new(cfg: Config, store: Store, tracker: Tracker) -> list[Job]:
    """Process every job currently in NEW. Returns the processed jobs."""
    jobs = store.by_status(Status.NEW)
    if not jobs:
        return []
    if cfg.backend == "cli" and len(jobs) > 1:
        return _process_batch(jobs, cfg, store, tracker)
    return [process_job(j, cfg, store, tracker) for j in jobs]
