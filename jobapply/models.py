"""Domain models shared across the pipeline."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class Status(str, Enum):
    NEW = "new"                      # captured, not yet tailored
    PROCESSING = "processing"        # tailoring/render in flight
    PENDING_REVIEW = "pending_review"  # tailored PDF ready, awaiting your decision
    APPROVED = "approved"            # you approved; apply page opened
    APPLIED = "applied"              # you confirmed you submitted it
    DENIED = "denied"                # you declined to apply
    ERROR = "error"                  # tailoring/render failed


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    # Identity / source
    id: str = ""                     # stable hash of the apply URL (dedupe key)
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""                    # canonical job / apply URL
    source: str = "linkedin"
    description: str = ""            # full JD text captured from the page

    # Pipeline state
    status: Status = Status.NEW
    fit_score: Optional[int] = None  # 0-100 from the fit-scorer
    fit_reasons: str = ""            # short rationale shown in review UI
    tailored_data_path: str = ""     # path to the tailored resume-data.json
    pdf_path: str = ""               # path to the rendered PDF
    error: str = ""

    # Bookkeeping
    captured_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self.make_id(self.url, self.title, self.company)
        if isinstance(self.status, str):
            self.status = Status(self.status)

    @staticmethod
    def make_id(url: str, title: str = "", company: str = "") -> str:
        """Dedupe key: prefer the URL; fall back to title+company if URL is missing."""
        basis = (url or f"{title}|{company}").strip().lower()
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Job":
        return cls(**row)
