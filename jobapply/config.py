"""Config loading. Reads config.yaml (falling back to config.example.yaml) and
resolves all paths relative to the repo root so the pipeline can be run from
anywhere."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # optional convenience dep; env vars still work without it
    def load_dotenv(*_a, **_k):
        return False

REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve(p: str) -> Path:
    """Resolve a config path relative to the repo root (absolute paths pass through)."""
    path = Path(p)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


class Config:
    def __init__(self, raw: dict[str, Any]):
        self._raw = raw
        paths = raw.get("paths", {})
        self.resume_creator = _resolve(paths["resume_creator"])
        self.master_resume_data = _resolve(paths["master_resume_data"])
        self.resume_html = _resolve(paths["resume_html"])
        self.data_dir = _resolve(paths["data_dir"])
        self.tracker_xlsx = _resolve(paths["tracker_xlsx"])

        claude = raw.get("claude", {})
        self.model = claude.get("model", "claude-sonnet-4-6")
        self.max_tokens = int(claude.get("max_tokens", 8000))
        # "cli" runs `claude -p` under your Claude subscription (no API credits);
        # "api" uses the Anthropic API (pay-per-token, needs ANTHROPIC_API_KEY).
        self.backend = claude.get("backend", "cli")
        self.cli_command = claude.get("cli_command", "claude")

        server = raw.get("server", {})
        self.host = server.get("host", "127.0.0.1")
        self.port = int(server.get("port", 8765))
        self.auto_process = bool(server.get("auto_process", True))

        self.criteria: dict[str, Any] = raw.get("criteria", {})

        # Derived runtime locations.
        self.db_path = self.data_dir / "jobs.db"
        self.pdf_dir = self.data_dir / "resumes"
        self.captures_dir = self.data_dir / "captures"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.pdf_dir, self.captures_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv(REPO_ROOT / ".env")
    if path is None:
        candidate = REPO_ROOT / "config.yaml"
        path = candidate if candidate.exists() else REPO_ROOT / "config.example.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    cfg = Config(raw)
    cfg.ensure_dirs()
    return cfg
