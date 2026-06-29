"""Local web server: job intake (from the LinkedIn bookmarklet) + the review
dashboard where you approve/deny each tailored resume and open the apply page."""
from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from .bookmarklet import install_page
from .config import Config
from .dashboard import DASHBOARD_HTML
from .models import Job, Status
from .pipeline import process_job
from .store import Store
from .tracker import Tracker


class CaptureIn(BaseModel):
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    source: str = "linkedin"
    description: str = ""


class CaptureBatchIn(BaseModel):
    jobs: list[CaptureIn] = []


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="Job Apply Pipeline")
    store = Store(cfg.db_path)
    tracker = Tracker(cfg.tracker_xlsx)

    # The bookmarklet runs on linkedin.com and POSTs here cross-origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://www.linkedin.com", "https://linkedin.com"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _background_process(job_id: str) -> None:
        job = store.get(job_id)
        if job:
            process_job(job, cfg, store, tracker)

    def _background_process_all() -> None:
        from .pipeline import process_new
        process_new(cfg, store, tracker)  # batches CLI tailoring into one call

    @app.post("/capture")
    def capture(payload: CaptureIn):
        if not payload.description.strip():
            raise HTTPException(400, "Missing job description.")
        job = Job(
            title=payload.title.strip(),
            company=payload.company.strip(),
            location=payload.location.strip(),
            url=payload.url.strip(),
            source=payload.source.strip() or "linkedin",
            description=payload.description.strip(),
        )
        is_new = store.upsert(job)
        if not is_new:
            return {"status": "duplicate", "id": job.id,
                    "message": "Already captured this job."}
        tracker.upsert(store.get(job.id))
        if cfg.auto_process:
            threading.Thread(target=_background_process, args=(job.id,),
                             daemon=True).start()
        return {"status": "captured", "id": job.id,
                "processing": cfg.auto_process}

    @app.post("/capture-batch")
    def capture_batch(payload: CaptureBatchIn):
        captured = 0
        for item in payload.jobs:
            if not item.description.strip():
                continue
            job = Job(
                title=item.title.strip(), company=item.company.strip(),
                location=item.location.strip(), url=item.url.strip(),
                source=item.source.strip() or "linkedin",
                description=item.description.strip(),
            )
            if store.upsert(job):
                tracker.upsert(store.get(job.id))
                captured += 1
        if captured and cfg.auto_process:
            # One background batch run (CLI tailoring batched into a single call).
            threading.Thread(target=_background_process_all, daemon=True).start()
        return {"status": "ok", "received": len(payload.jobs),
                "captured": captured, "processing": bool(captured and cfg.auto_process)}

    @app.get("/api/jobs")
    def api_jobs():
        return JSONResponse([
            {k: v for k, v in j.to_row().items() if k != "description"}
            for j in store.all()
        ])

    @app.get("/pdf/{job_id}")
    def pdf(job_id: str):
        job = store.get(job_id)
        if not job or not job.pdf_path:
            raise HTTPException(404, "No PDF for this job yet.")
        return FileResponse(job.pdf_path, media_type="application/pdf",
                            filename=f"{job.company}-{job.title}.pdf".replace("/", "-"))

    @app.post("/approve/{job_id}")
    def approve(job_id: str):
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job.")
        store.set_status(job_id, Status.APPROVED)
        tracker.upsert(store.get(job_id))
        # The dashboard opens this URL in a new tab so you can submit yourself.
        return {"status": "approved", "apply_url": job.url}

    @app.post("/applied/{job_id}")
    def applied(job_id: str):
        store.set_status(job_id, Status.APPLIED)
        tracker.upsert(store.get(job_id))
        return {"status": "applied"}

    @app.post("/deny/{job_id}")
    def deny(job_id: str):
        store.set_status(job_id, Status.DENIED)
        tracker.upsert(store.get(job_id))
        return {"status": "denied"}

    @app.post("/process/{job_id}")
    def process_one(job_id: str):
        if not store.get(job_id):
            raise HTTPException(404, "Unknown job.")
        threading.Thread(target=_background_process, args=(job_id,),
                         daemon=True).start()
        return {"status": "processing"}

    @app.get("/bookmarklet", response_class=HTMLResponse)
    def bookmarklet():
        return install_page(f"http://{cfg.host}:{cfg.port}")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return DASHBOARD_HTML

    return app
