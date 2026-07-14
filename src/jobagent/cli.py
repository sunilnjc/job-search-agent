from __future__ import annotations

from typing import Optional

import typer

from jobagent.config import settings
from jobagent.profile.resume_parser import parse_resume
from jobagent.service import run_fetch, run_match, run_prepare, slugify
from jobagent.storage import db
from jobagent.tracking import pipeline
from jobagent.drafting.cover_letter import build_cover_letter_pdf, draft_cover_letter
from jobagent.drafting.gap_analysis import analyze_gaps
from jobagent.drafting.resume_builder import (
    build_tailored_resume,
    build_tailored_resume_pdf,
    parse_tailoring_notes,
)
from jobagent.drafting.resume_tailor import draft_resume_tailoring

app = typer.Typer(help="Personal job search agent: fetch, match, draft, track.")


@app.command()
def fetch(url: Optional[str] = typer.Option(None, "--url", help="Fetch a single job posting by URL (LinkedIn/Indeed etc.)")):
    """Pull job postings from all configured sources, or a single manually-found URL."""
    run_fetch(url, on_progress=typer.echo)


@app.command()
def match(limit: Optional[int] = typer.Option(None, "--limit", help="Only score this many unscored jobs")):
    """Score fetched jobs against your resume: embedding prefilter + Ollama fit rating."""
    run_match(limit, on_progress=typer.echo)


@app.command()
def prepare(top: int = typer.Option(3, "--top", help="How many top new matches to draft materials for")):
    """Hands-off daily prep: fetch + match + draft the top N new matches for review."""
    run_prepare(top, on_progress=typer.echo)


@app.command()
def review(limit: int = 10):
    """List top-ranked matched jobs."""
    with db.connection() as conn:
        rows = db.top_ranked_jobs(conn, limit=limit)
        if not rows:
            typer.echo("No matched jobs yet. Run `jobagent fetch` then `jobagent match` first.")
            return
        eligibility_flags = {"worldwide": "REMOTE-ANYWHERE", "sponsors": "SPONSORS-VISA", "unknown": "eligibility unclear"}
        for row in rows:
            flag = eligibility_flags.get(row["eligibility"], row["eligibility"])
            typer.echo(
                f"[{row['id']}] score={row['llm_score']} [{flag}] — {row['title']} @ {row['company']} "
                f"({row['location']})\n    {row['url']}\n    reasoning: {row['llm_reasoning']}\n"
            )


@app.command()
def draft(job_id: int):
    """Generate a tailored cover letter and resume bullets for a job (uses Claude API)."""
    db.init_db()
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            typer.echo(f"No job with id {job_id}")
            raise typer.Exit(1)

        profile = parse_resume()
        typer.echo("Drafting cover letter...")
        cover_letter = draft_cover_letter(profile, job["title"], job["company"], job["description"])
        typer.echo("Drafting resume tailoring notes...")
        resume_notes = draft_resume_tailoring(profile, job["title"], job["company"], job["description"])

        folder_name = slugify(f"{job['company']}-{job['title']}")
        out_dir = settings.output_dir / folder_name
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "cover_letter.md").write_text(cover_letter)
        (out_dir / "resume_tailoring.md").write_text(resume_notes)
        build_cover_letter_pdf(cover_letter, out_dir / "cover_letter.pdf")

        summary, highlights = parse_tailoring_notes(resume_notes)
        if summary and highlights:
            build_tailored_resume(summary, highlights, out_dir / "tailored_resume.docx", job_title=job["title"])
            build_tailored_resume_pdf(summary, highlights, out_dir / "tailored_resume.pdf", job_title=job["title"])
        else:
            typer.echo("Warning: could not parse tailoring notes, skipping tailored resume files")

        pipeline.transition(conn, job_id, "drafted")

    typer.echo(f"Wrote drafts to {out_dir}")


@app.command()
def gaps(job_id: int):
    """Compare your resume against a specific posting: missing requirements/keywords before you apply."""
    db.init_db()
    with db.connection() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            typer.echo(f"No job with id {job_id}")
            raise typer.Exit(1)

        profile = parse_resume()
        typer.echo(f"Analyzing gaps for: {job['title']} @ {job['company']}\n")
        report = analyze_gaps(profile, job["title"], job["company"], job["location"], job["description"])
        typer.echo(report)

        folder_name = slugify(f"{job['company']}-{job['title']}")
        out_dir = settings.output_dir / folder_name
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "gap_analysis.md").write_text(report)
        typer.echo(f"\nSaved to {out_dir / 'gap_analysis.md'}")


@app.command()
def status(job_id: Optional[int] = typer.Argument(None), new_status: Optional[str] = typer.Argument(None)):
    """View pipeline status counts, or update a job's status."""
    db.init_db()
    with db.connection() as conn:
        if job_id is not None and new_status is not None:
            pipeline.transition(conn, job_id, new_status)
            typer.echo(f"Job {job_id} -> {new_status}")
            return

        counts = pipeline.summarize(conn)
        for stage, count in counts.items():
            typer.echo(f"{stage:15s} {count}")


if __name__ == "__main__":
    app()
