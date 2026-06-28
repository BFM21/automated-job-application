"""Claude tailoring + fit scoring.

Given the master resume data, a captured job, and your criteria, Claude:
  1. scores how well the job fits you (0-100) with a short rationale, and
  2. produces a tailored resume-data.json (same schema as the master) that
     re-emphasizes and rephrases your REAL experience toward this job.

Two interchangeable backends (config `claude.backend`):
  - "cli"  → shells out to `claude -p` under your Claude subscription. No API
             credits spent; subject to your plan's usage limits.
  - "api"  → calls the Anthropic API (pay-per-token; needs ANTHROPIC_API_KEY).

Hard rule baked into the prompt: never invent experience, employers, dates,
titles, or skills you don't have. Tailoring = selection + emphasis + phrasing,
not fabrication. Identity fields (photo, contact, settings) are restored from
the master afterwards so they can't drift.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import Config
from .models import Job

# Sections Claude may rewrite. Everything else is copied from the master verbatim.
TAILORABLE = ("personal", "experience", "education", "skills", "projects",
              "languages", "certifications")
# Fields force-restored from the master after tailoring (no drift allowed).
LOCKED_PERSONAL = ("name", "email", "linkedin", "website", "photo")

_SYSTEM = """You are an expert resume tailor and recruiter. You adapt a candidate's \
EXISTING resume to a specific job. You may reorder items, rephrase bullet points to \
mirror the job's language, choose which true accomplishments to emphasize, and adjust \
the headline title to match the target role IF it is honest. You must NOT invent \
employers, roles, dates, degrees, certifications, or skills the candidate does not \
have. If the candidate lacks something the job wants, leave it out rather than fake \
it. Preserve the exact JSON schema of the input resume data, including all `id` \
fields."""

# Output contract, shared by both backends. The API backend enforces it with a tool
# schema; the CLI backend asks for it in-prompt and we parse the JSON back out.
_OUTPUT_KEYS = ("fit_score", "fit_reasons", "tailored_resume_data")

_TOOL = {
    "name": "submit_tailored_resume",
    "description": "Return the fit score and the tailored resume data.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fit_score": {
                "type": "integer", "minimum": 0, "maximum": 100,
                "description": "How well this job matches the candidate's profile and stated criteria.",
            },
            "fit_reasons": {
                "type": "string",
                "description": "2-3 sentences: why this score, key matches and gaps.",
            },
            "tailored_resume_data": {
                "type": "object",
                "description": "Full resume data, same schema as the master, re-emphasized for this job.",
                "additionalProperties": True,
            },
        },
        "required": list(_OUTPUT_KEYS),
    },
}

_JSON_INSTRUCTION = """## Output format
Respond with ONLY a single JSON object and nothing else — no markdown, no code \
fences, no commentary before or after. The object must have exactly these top-level \
keys:
- "fit_score": integer 0-100
- "fit_reasons": string (2-3 sentences on the score, key matches and gaps)
- "tailored_resume_data": object (the full resume, same schema and `id` fields as the \
master, re-emphasized for this job)"""


def _build_user_prompt(master: dict, job: Job, criteria: dict) -> str:
    return (
        "## Candidate's master resume data (JSON)\n```json\n"
        + json.dumps(master, ensure_ascii=False, indent=2)
        + "\n```\n\n## Candidate's job-search criteria\n```json\n"
        + json.dumps(criteria, ensure_ascii=False, indent=2)
        + "\n```\n\n## Target job\n"
        + f"Title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        + f"URL: {job.url}\n\nDescription:\n{job.description}\n\n"
        "## Task\n"
        "1. Score 0-100 how well this job fits the candidate, weighting the criteria's "
        "must_have heavily and penalizing anything in `avoid`.\n"
        "2. Produce `tailored_resume_data`: the same resume, same schema and ids, "
        "re-emphasized and rephrased for THIS job. Keep it truthful."
    )


# ── backends ─────────────────────────────────────────────────────────────────
def _run_api(system: str, user: str, cfg: Config) -> dict[str, Any]:
    """Anthropic API backend (pay-per-token). Uses forced tool use for valid JSON."""
    import anthropic  # imported lazily so the CLI backend needs no API SDK key/creds

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    resp = client.messages.create(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        system=system,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_tailored_resume"},
        messages=[{"role": "user", "content": user}],
    )
    tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Claude (API) did not return tailored resume data.")
    return tool_use.input


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of free-form model text (tolerates fences/prose)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError(f"No JSON object found in model output: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def _run_cli(system: str, user: str, cfg: Config) -> dict[str, Any]:
    """Claude Code CLI backend (`claude -p`) — runs under your subscription."""
    exe = shutil.which(cfg.cli_command) or cfg.cli_command  # resolves claude.cmd on Windows
    prompt = f"{system}\n\n{user}\n\n{_JSON_INSTRUCTION}"
    cmd = [exe, "-p", "--output-format", "json", "--model", cfg.model]
    # Windows can't CreateProcess a .cmd/.bat shim directly — route it through cmd.exe.
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", *cmd]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Could not run '{cfg.cli_command}'. Is Claude Code installed and on PATH? "
            "Install it, or set claude.cli_command in config.yaml."
        ) from exc

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
        )

    out_text = proc.stdout.strip()
    # --output-format json wraps the reply in an envelope; unwrap to the model text.
    try:
        envelope = json.loads(out_text)
        if isinstance(envelope, dict) and "result" in envelope:
            if envelope.get("is_error"):
                raise RuntimeError(f"claude CLI error: {str(envelope.get('result'))[:500]}")
            out_text = str(envelope["result"])
    except json.JSONDecodeError:
        pass  # stdout was already the raw model text

    return _extract_json(out_text)


_BACKENDS = {"cli": _run_cli, "api": _run_api}


# ── orchestration ────────────────────────────────────────────────────────────
def _load_master(cfg: Config) -> dict:
    with open(cfg.master_resume_data, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _restore_identity(tailored: dict, master: dict) -> dict:
    """Force identity + settings back to the master values so they can't drift."""
    tailored.setdefault("personal", {})
    for f in LOCKED_PERSONAL:
        if f in master.get("personal", {}):
            tailored["personal"][f] = master["personal"][f]
    tailored["settings"] = master.get("settings", tailored.get("settings", {}))
    # Guarantee every section exists; fall back to master for any Claude dropped.
    for key in TAILORABLE:
        if key not in tailored:
            tailored[key] = master.get(key)
    return tailored


def enrich(job: Job, cfg: Config) -> tuple[int, str, Path]:
    """Tailor the resume for `job`. Returns (fit_score, fit_reasons, tailored_json_path)."""
    backend = _BACKENDS.get(cfg.backend)
    if backend is None:
        raise RuntimeError(f"Unknown claude.backend '{cfg.backend}' (use 'cli' or 'api').")

    master = _load_master(cfg)

    # Strip the (large, base64) photo before sending to save tokens; restored after.
    sent = json.loads(json.dumps(master))
    photo = sent.get("personal", {}).pop("photo", None)

    out = backend(_SYSTEM, _build_user_prompt(sent, job, cfg.criteria), cfg)
    missing = [k for k in _OUTPUT_KEYS if k not in out]
    if missing:
        raise RuntimeError(f"Backend output missing keys: {missing}")

    fit_score = int(out["fit_score"])
    fit_reasons = str(out["fit_reasons"]).strip()
    tailored = out["tailored_resume_data"]

    if photo is not None:
        master.setdefault("personal", {})["photo"] = photo
    tailored = _restore_identity(tailored, master)

    out_path = cfg.pdf_dir / f"{job.id}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(tailored, fh, ensure_ascii=False, indent=2)

    return fit_score, fit_reasons, out_path
