# Automated Job Application Pipeline

A human-in-the-loop pipeline modeled on the approach that actually works (tailored
applications + manual review beat spray-and-pray): you capture jobs from LinkedIn,
Claude tailors your résumé and scores the fit, a PDF is rendered from your existing
[resume_creator](../resume_creator) app, and **you approve each one** before applying.
Every application is logged to an Excel tracker.

```
LinkedIn (you, logged in)
      │  click bookmarklet
      ▼
  /capture ──► SQLite (dedupe) ──► Claude tailor + fit score ──► Playwright PDF
                                                                      │
                                       Review dashboard  ◄────────────┘
                                       (approve / deny)
                                            │ approve
                                            ▼
                                  apply page opens — you submit
                                            │
                                            ▼
                                   applications.xlsx (tracker)
```

Nothing is submitted automatically. The pipeline never touches your LinkedIn
account — you stay in your own browser session the whole time.

## Setup

```bash
cd automated_job_application
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
playwright install chromium                          # one-time browser download

python run.py init                                   # creates config.yaml
# edit config.yaml: fill in `criteria:` and check the paths to ../resume_creator
echo ANTHROPIC_API_KEY=sk-ant-... > .env             # your API key
```

## Use

```bash
python run.py serve
```

1. Open <http://127.0.0.1:8765/bookmarklet> once and drag **📌 Capture job** to your
   bookmarks bar.
2. Browse LinkedIn jobs as normal. On a posting you like, click the bookmark — it
   grabs the title/company/JD from the page and sends it to the pipeline.
3. With `auto_process: true`, Claude tailors your résumé and renders a PDF in the
   background. Watch them appear at <http://127.0.0.1:8765/>.
4. In the dashboard: **Preview résumé**, read the fit score/reasons, then
   **Approve & open** (opens the posting so you submit yourself) or **Deny**.
5. After you submit, hit **Mark applied**. Everything lands in
   `data/applications.xlsx`.

## CLI

| Command | What it does |
|---|---|
| `python run.py serve` | Start the capture + review web server |
| `python run.py process` | Tailor + render all NEW jobs (manual batch) |
| `python run.py capture -f job.json` | Add a job from a JSON file (paste fallback) |
| `python run.py list` | List all tracked jobs |

## Layout

```
run.py                 CLI entrypoint
config.example.yaml    copy to config.yaml; holds paths + your job criteria
jobapply/
  config.py            config + path resolution
  models.py            Job model + Status enum
  store.py             SQLite store (dedupe + state, thread-safe)
  tracker.py           Excel application tracker (openpyxl)
  enrich.py            Claude: fit score + tailored resume-data.json
  render.py            Playwright: inject data into resume_creator -> PDF
  web.py               FastAPI server: /capture, dashboard, approve/deny/apply
  dashboard.py         the review UI (HTML/JS)
  bookmarklet.py       LinkedIn capture bookmarklet + install page
  pipeline.py          NEW -> tailor -> render -> PENDING_REVIEW
data/                  runtime (gitignored): jobs.db, resumes/, applications.xlsx
```

## Notes & next steps

- **Truthfulness:** the tailoring prompt forbids inventing experience, employers,
  dates, or skills — it only re-emphasizes and rephrases what's already in your
  master résumé data.
- **Other sources** (Greenhouse / Lever / Ashby / company boards): drop additional
  capture adapters that build the same `Job` and call `store.upsert` — the rest of
  the pipeline is source-agnostic.
- **LinkedIn DOM** selectors in `bookmarklet.py` may need tweaking if LinkedIn
  changes its markup.
