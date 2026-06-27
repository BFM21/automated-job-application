"""Claude tailoring + fit scoring.

Given the master resume data, a captured job, and your criteria, Claude:
  1. scores how well the job fits you (0-100) with a short rationale, and
  2. produces a tailored resume-data.json (same schema as the master) that
     re-emphasizes and rephrases your REAL experience toward this job.

Hard rule baked into the prompt: never invent experience, employers, dates,
titles, or skills you don't have. Tailoring = selection + emphasis + phrasing,
not fabrication. Identity fields (photo, contact, settings) are restored from
the master afterwards so they can't drift.
"""
from __future__ import annotations

import json
from pathlib import Path

import anthropic

from .config import Config
from .models import Job

# Sections Claude may rewrite. Everything else is copied from the master verbatim.
TAILORABLE = ("personal", "experience", "education", "skills", "projects",
              "languages", "certifications")
# Fields force-restored from the master after tailoring (no drift allowed).
LOCKED_PERSONAL = ("name", "email", "linkedin", "website", "photo")

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
        "required": ["fit_score", "fit_reasons", "tailored_resume_data"],
    },
}

_SYSTEM = """You are an expert resume tailor and recruiter. You adapt a candidate's \
EXISTING resume to a specific job. You may reorder items, rephrase bullet points to \
mirror the job's language, choose which true accomplishments to emphasize, and adjust \
the headline title to match the target role IF it is honest. You must NOT invent \
employers, roles, dates, degrees, certifications, or skills the candidate does not \
have. If the candidate lacks something the job wants, leave it out rather than fake \
it. Preserve the exact JSON schema of the input resume data, including all `id` \
fields. Return your answer only via the submit_tailored_resume tool."""


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
    master = _load_master(cfg)

    # Strip the (large, base64) photo before sending to save tokens; restored after.
    sent = json.loads(json.dumps(master))
    photo = sent.get("personal", {}).pop("photo", None)

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    resp = client.messages.create(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        system=_SYSTEM,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "submit_tailored_resume"},
        messages=[{"role": "user", "content": _build_user_prompt(sent, job, cfg.criteria)}],
    )

    tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise RuntimeError("Claude did not return tailored resume data.")
    out = tool_use.input

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
