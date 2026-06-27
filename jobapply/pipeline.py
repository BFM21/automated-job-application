"""Glue: take a captured job from NEW -> PENDING_REVIEW by tailoring then rendering."""
from __future__ import annotations

import traceback

from .config import Config
from .enrich import enrich
from .models import Job, Status
from .render import render_pdf
from .store import Store
from .tracker import Tracker


def process_job(job: Job, cfg: Config, store: Store, tracker: Tracker) -> Job:
    """Tailor + render a single job. Updates the store and tracker. Never raises;
    failures land the job in ERROR with the message recorded."""
    store.set_status(job.id, Status.PROCESSING)
    try:
        fit_score, fit_reasons, json_path = enrich(job, cfg)
        pdf_path = render_pdf(job, json_path, cfg)
        job.fit_score = fit_score
        job.fit_reasons = fit_reasons
        job.tailored_data_path = str(json_path)
        job.pdf_path = str(pdf_path)
        job.status = Status.PENDING_REVIEW
        job.error = ""
    except Exception as exc:  # noqa: BLE001 - record and move on
        job.status = Status.ERROR
        job.error = f"{exc}\n{traceback.format_exc()}"
    store.save(job)
    tracker.upsert(job)
    return job


def process_new(cfg: Config, store: Store, tracker: Tracker) -> list[Job]:
    """Process every job currently in NEW. Returns the processed jobs."""
    jobs = store.by_status(Status.NEW)
    return [process_job(j, cfg, store, tracker) for j in jobs]
