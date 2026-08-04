from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from jobagent.api import runs
from jobagent.api.schemas import (
    ChatRequest,
    ChatResponse,
    ExcludeUpdate,
    JobDetailOut,
    JobOut,
    RunOut,
    RunTrigger,
    StatusUpdate,
)
from jobagent.config import settings
from jobagent.drafting.application_chat import build_system_prompt, run_application_chat
from jobagent.drafting.cover_letter import build_cover_letter_pdf, draft_cover_letter
from jobagent.drafting.gap_analysis import analyze_gaps
from jobagent.drafting.resume_builder import (
    build_tailored_resume,
    build_tailored_resume_pdf,
    parse_tailoring_notes,
)
from jobagent.drafting.resume_tailor import draft_resume_tailoring
from jobagent.models import STATUSES
from jobagent.profile.resume_parser import parse_resume
from jobagent.service import slugify
from jobagent.storage import db
from jobagent.tracking import pipeline

logger = logging.getLogger(__name__)

# <repo root>/web/dist — main.py lives at <repo root>/src/jobagent/api/main.py
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

app = FastAPI(title="Job Search Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()

# Parsed once at startup and reused — resume parsing is an LLM call, no need to
# repeat it on every draft/gaps request in a long-lived server process.
_profile = None


def get_profile():
    global _profile
    if _profile is None:
        _profile = parse_resume()
    return _profile


def _row_to_job(row) -> JobOut:
    return JobOut(
        id=row["id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        remote=bool(row["remote"]),
        country=row["country"] if "country" in row.keys() else None,
        url=row["url"],
        description=row["description"],
        salary=row["salary"],
        status=row["status"],
        llm_score=row["llm_score"] if "llm_score" in row.keys() else None,
        llm_reasoning=row["llm_reasoning"] if "llm_reasoning" in row.keys() else None,
        embedding_similarity=row["embedding_similarity"] if "embedding_similarity" in row.keys() else None,
        eligibility=row["eligibility"] if "eligibility" in row.keys() and row["eligibility"] else "unknown",
        excluded_reason=row["excluded_reason"] if "excluded_reason" in row.keys() else None,
    )


def _read_artifact(job_row, filename: str) -> str | None:
    folder = slugify(f"{job_row['company']}-{job_row['title']}")
    path = settings.output_dir / folder / filename
    return path.read_text() if path.exists() else None


@app.get("/api/jobs", response_model=list[JobOut])
def list_jobs():
    with db.connection() as conn:
        return [_row_to_job(r) for r in db.list_jobs_with_scores(conn)]


@app.get("/api/jobs/{job_id}", response_model=JobDetailOut)
def get_job(job_id: int):
    with db.connection() as conn:
        row = db.get_job(conn, job_id)
        if not row:
            raise HTTPException(404, f"No job with id {job_id}")
        scored = next((r for r in db.list_jobs_with_scores(conn) if r["id"] == job_id), row)
        base = _row_to_job(scored)
        folder = slugify(f"{row['company']}-{row['title']}")
        artifact_dir = settings.output_dir / folder
        return JobDetailOut(
            **base.model_dump(),
            cover_letter=_read_artifact(row, "cover_letter.md"),
            resume_tailoring=_read_artifact(row, "resume_tailoring.md"),
            gap_analysis=_read_artifact(row, "gap_analysis.md"),
            has_resume_docx=(artifact_dir / "tailored_resume.docx").exists(),
            has_resume_pdf=(artifact_dir / "tailored_resume.pdf").exists(),
            has_cover_letter_pdf=(artifact_dir / "cover_letter.pdf").exists(),
        )


@app.patch("/api/jobs/{job_id}/status")
def update_status(job_id: int, body: StatusUpdate):
    if body.status not in STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {', '.join(STATUSES)}")
    with db.connection() as conn:
        try:
            pipeline.transition(conn, job_id, body.status)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
    return {"id": job_id, "status": body.status}


@app.patch("/api/jobs/{job_id}/exclude")
def exclude_job(job_id: int, body: ExcludeUpdate):
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, f"No job with id {job_id}")
        db.set_excluded(conn, job_id, body.reason)
    return {"id": job_id, "excluded_reason": body.reason}


@app.patch("/api/jobs/{job_id}/unexclude")
def unexclude_job(job_id: int):
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, f"No job with id {job_id}")
        db.set_excluded(conn, job_id, None)
    return {"id": job_id, "excluded_reason": None}


@app.post("/api/jobs/{job_id}/draft")
def generate_draft(job_id: int):
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, f"No job with id {job_id}")

        profile = get_profile()
        cover_letter = draft_cover_letter(profile, job["title"], job["company"], job["description"])
        resume_notes = draft_resume_tailoring(profile, job["title"], job["company"], job["description"])

        folder = slugify(f"{job['company']}-{job['title']}")
        out_dir = settings.output_dir / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "cover_letter.md").write_text(cover_letter)
        (out_dir / "resume_tailoring.md").write_text(resume_notes)
        build_cover_letter_pdf(cover_letter, out_dir / "cover_letter.pdf")

        summary, highlights = parse_tailoring_notes(resume_notes)
        has_resume_docx = bool(summary and highlights)
        if has_resume_docx:
            build_tailored_resume(summary, highlights, out_dir / "tailored_resume.docx", job_title=job["title"])
            build_tailored_resume_pdf(summary, highlights, out_dir / "tailored_resume.pdf", job_title=job["title"])

        pipeline.transition(conn, job_id, "drafted")

    return {
        "cover_letter": cover_letter,
        "resume_tailoring": resume_notes,
        "has_resume_docx": has_resume_docx,
    }


@app.get("/api/jobs/{job_id}/resume-docx")
def download_resume_docx(job_id: int):
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, f"No job with id {job_id}")

        folder = slugify(f"{job['company']}-{job['title']}")
        path = settings.output_dir / folder / "tailored_resume.docx"
        if not path.exists():
            raise HTTPException(404, "No tailored resume generated yet for this job")

        filename = f"Sunilkumar Kalabandi - {job['company']}.docx"
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=filename,
        )


@app.get("/api/jobs/{job_id}/resume-pdf")
def download_resume_pdf(job_id: int):
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, f"No job with id {job_id}")

        folder = slugify(f"{job['company']}-{job['title']}")
        path = settings.output_dir / folder / "tailored_resume.pdf"
        if not path.exists():
            raise HTTPException(404, "No tailored resume PDF generated yet for this job")

        filename = f"Sunilkumar Kalabandi - {job['company']}.pdf"
        return FileResponse(path, media_type="application/pdf", filename=filename)


@app.get("/api/jobs/{job_id}/cover-letter-pdf")
def download_cover_letter_pdf(job_id: int):
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, f"No job with id {job_id}")

        folder = slugify(f"{job['company']}-{job['title']}")
        path = settings.output_dir / folder / "cover_letter.pdf"
        if not path.exists():
            raise HTTPException(404, "No cover letter PDF generated yet for this job")

        filename = f"Sunilkumar Kalabandi - Cover Letter - {job['company']}.pdf"
        return FileResponse(path, media_type="application/pdf", filename=filename)


@app.post("/api/jobs/{job_id}/gaps")
def generate_gaps(job_id: int):
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, f"No job with id {job_id}")

        profile = get_profile()
        report = analyze_gaps(profile, job["title"], job["company"], job["location"], job["description"])

        folder = slugify(f"{job['company']}-{job['title']}")
        out_dir = settings.output_dir / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "gap_analysis.md").write_text(report)

    return {"gap_analysis": report}


@app.post("/api/jobs/{job_id}/chat", response_model=ChatResponse)
def application_chat(job_id: int, body: ChatRequest):
    """Answer application questions for this specific role, grounded in the resume and
    this job's generated cover letter / tailoring notes, using the job as company context."""
    if not body.messages:
        raise HTTPException(400, "No messages provided")

    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            raise HTTPException(404, f"No job with id {job_id}")

    system_prompt = build_system_prompt(
        resume_text=get_profile().raw_text,
        title=job["title"],
        company=job["company"],
        location=job["location"],
        job_description=job["description"],
        cover_letter=_read_artifact(job, "cover_letter.md"),
        resume_tailoring=_read_artifact(job, "resume_tailoring.md"),
    )

    out_dir = settings.output_dir / slugify(f"{job['company']}-{job['title']}")

    def apply_cover_letter(new_text: str) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "cover_letter.md").write_text(new_text)
        build_cover_letter_pdf(new_text, out_dir / "cover_letter.pdf")

    def apply_tailored_resume(summary: str, highlights: list[str]) -> bool:
        """Persist structured AI edits and only then replace the downloadable resume files."""
        if not summary or not 4 <= len(highlights) <= 6:
            return False

        out_dir.mkdir(parents=True, exist_ok=True)
        notes = "## Tailored Professional Summary\n\n" + summary.strip() + "\n\n## Tailored Bullet Points\n\n"
        notes += "\n".join(f"- {highlight.strip()}" for highlight in highlights)
        (out_dir / "resume_tailoring.md").write_text(notes + "\n")
        build_tailored_resume(summary, highlights, out_dir / "tailored_resume.docx", job_title=job["title"])
        build_tailored_resume_pdf(summary, highlights, out_dir / "tailored_resume.pdf", job_title=job["title"])
        return True

    reply, updated = run_application_chat(
        system_prompt,
        [m.model_dump() for m in body.messages],
        apply_cover_letter,
        apply_tailored_resume,
    )
    return ChatResponse(reply=reply, materials_updated=updated)


@app.get("/api/status/summary")
def status_summary():
    with db.connection() as conn:
        return pipeline.summarize(conn)


@app.post("/api/runs/fetch", response_model=RunOut)
def trigger_fetch(body: RunTrigger):
    run_id = runs.start_fetch(url=body.url)
    return RunOut(run_id=run_id, status="running", log=[])


@app.post("/api/runs/match", response_model=RunOut)
def trigger_match(body: RunTrigger):
    run_id = runs.start_match(limit=body.limit)
    return RunOut(run_id=run_id, status="running", log=[])


@app.get("/api/runs/{run_id}", response_model=RunOut)
def get_run_status(run_id: str):
    run = runs.get_run(run_id)
    if not run:
        raise HTTPException(404, f"No run with id {run_id}")
    return RunOut(run_id=run_id, status=run["status"], log=run["log"])


# ---------------------------------------------------------------------------
# Built frontend (web/dist), served from this same process so a phone only needs
# one host:port. Must stay BELOW every /api route: routes match in definition
# order, so the catch-all below only ever sees paths the API didn't claim.
# ---------------------------------------------------------------------------

_index_html = WEB_DIST / "index.html"

if _index_html.is_file():
    _dist_root = WEB_DIST.resolve()

    if (WEB_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        """Serve a real file out of web/dist when one exists, else index.html so
        client-side routes survive a hard reload."""
        if full_path.startswith("api/"):
            raise HTTPException(404, "Not found")

        if full_path:
            candidate = (WEB_DIST / full_path).resolve()
            # Reject traversal (`..`) escaping web/dist before touching the path.
            if _dist_root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)

        return FileResponse(_index_html)

else:
    logger.warning(
        "Frontend not built: %s missing. Serving API only — run `npm run build` in web/ "
        "to enable the single-port UI.",
        _index_html,
    )
