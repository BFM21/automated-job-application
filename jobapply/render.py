"""Render a tailored resume to PDF by driving the existing resume_creator web app
headlessly. The app reads its data from localStorage['resumeData'], so we inject
the tailored JSON there, reload, and print to PDF using print media (which the
app's @media print CSS already styles for a clean single-resume page)."""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import Config
from .models import Job


def render_pdf(job: Job, tailored_json_path: Path, cfg: Config) -> Path:
    with open(tailored_json_path, "r", encoding="utf-8") as fh:
        data_str = fh.read()
    json.loads(data_str)  # validate it parses before launching a browser

    pdf_path = cfg.pdf_dir / f"{job.id}.pdf"
    page_url = cfg.resume_html.as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            # Establish the file:// origin, seed localStorage, then reload so the
            # app boots with the tailored data.
            page.goto(page_url, wait_until="load")
            page.evaluate("d => localStorage.setItem('resumeData', d)", data_str)
            page.goto(page_url, wait_until="networkidle")
            page.wait_for_selector(".preview-page", state="attached", timeout=15000)
            page.emulate_media(media="print")
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
        finally:
            browser.close()

    return pdf_path
