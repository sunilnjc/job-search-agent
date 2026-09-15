from __future__ import annotations

from typing import Callable, Optional

from jobagent.config import settings
from jobagent.matching.eligibility import classify
from jobagent.matching.embeddings import cosine_similarity, embed
from jobagent.matching.ollama_rank import rank_job, valid_score
from jobagent.matching.constraints import review_reasons
from jobagent.models import MatchScore
from jobagent.profile.resume_parser import parse_resume
from jobagent.sources.adzuna import AdzunaSource
from jobagent.sources.ats_boards import ATSBoardsSource
from jobagent.sources.manual_url import ManualURLSource
from jobagent.sources.remoteok import RemoteOKSource
from jobagent.sources.weworkremotely import WeWorkRemotelySource
from jobagent.sources.validation import check_live_job_link
from jobagent.storage import db
from jobagent.tracking import pipeline

def configured_bulk_sources():
    """Direct boards are the default. Aggregator feeds are explicit opt-ins only."""
    sources = []
    if settings.enable_discovery_feeds:
        sources.extend([RemoteOKSource(), WeWorkRemotelySource()])
        if settings.enable_adzuna:
            sources.append(AdzunaSource())
    sources.append(ATSBoardsSource())
    return sources

LLM_SCORE_THRESHOLD = 6
EMBEDDING_SIMILARITY_FLOOR = 0.2

SPONSORSHIP_EXCLUSION_REASON = (
    "Role in a country where you lack work authorization (incl. domestic-remote); "
    "no visa sponsorship mentioned"
)

ProgressFn = Callable[[str], None]


def apply_sponsorship_exclusions(conn, on_progress: ProgressFn = print) -> int:
    """Retire the former sponsorship auto-exclusion rule.

    A missing sponsorship statement is not proof that a company will reject the
    candidate. Keep the eligibility label, but let the actual ATS form collect the
    truthful work-authorisation answer. Existing jobs excluded by the old rule are
    restored so they can be scored and reviewed again.
    """
    restored = conn.execute(
        """UPDATE jobs SET excluded_reason = NULL
           WHERE excluded_reason = ? AND status IN ('new', 'matched', 'drafted')""",
        (SPONSORSHIP_EXCLUSION_REASON,),
    ).rowcount
    if restored:
        on_progress(f"Restored {restored} role(s) previously hidden for unconfirmed sponsorship.")
    return restored


def run_fetch(url: Optional[str] = None, on_progress: ProgressFn = print) -> int:
    """Pull job postings from all configured sources, or a single manually-found URL.

    Returns the number of postings processed.
    """
    db.init_db()

    if url:
        posting = ManualURLSource().fetch_by_url(url)
        with db.connection() as conn:
            job_id = db.upsert_job(conn, posting)
        on_progress(f"Fetched 1 job from {url} -> job id {job_id}")
        return 1

    total = 0
    with db.connection() as conn:
        for source in configured_bulk_sources():
            try:
                postings = source.search()
            except Exception as exc:  # noqa: BLE001 - one source failing shouldn't kill the run
                on_progress(f"[{source.name}] failed: {exc}")
                continue
            for posting in postings:
                db.upsert_job(conn, posting)
            # Direct ATS feeds are authoritative for only their own board. Reconcile
            # successful boards so old Adyen-style records disappear on the next refresh.
            if isinstance(source, ATSBoardsSource):
                for board_source in source.successful_sources:
                    live_urls = {posting.url for posting in postings if posting.source == board_source}
                    retired = db.reconcile_direct_source(conn, board_source, live_urls)
                    if retired:
                        on_progress(f"[{board_source}] excluded {retired} role(s) no longer listed directly.")
            on_progress(f"[{source.name}] fetched {len(postings)} postings")
            total += len(postings)
        apply_sponsorship_exclusions(conn, on_progress)
        # Existing Adzuna rows remain auditable in Excluded, but are never shown as
        # actionable after the user chose a direct-employer-only application board.
        hidden_aggregators = conn.execute(
            """
            UPDATE jobs SET excluded_reason = 'Aggregator listing — use a direct employer careers page instead'
            WHERE source IN ('adzuna', 'remoteok', 'weworkremotely') AND excluded_reason IS NULL
              AND status IN ('new', 'matched', 'drafted')
            """
        ).rowcount
        if hidden_aggregators:
            on_progress(f"Excluded {hidden_aggregators} aggregator listing(s) from the active board.")
        duplicates = db.exclude_duplicate_aggregator_listings(conn)
        if duplicates:
            on_progress(f"Excluded {duplicates} duplicate discovery-feed listings.")
    on_progress(f"Done. {total} postings processed.")
    return total


def run_match(limit: Optional[int] = None, on_progress: ProgressFn = print, direct_only: bool = False) -> None:
    """Score fetched jobs against the resume: title filter + eligibility + embedding + LLM rating."""
    db.init_db()
    profile = parse_resume()
    resume_embedding = embed(profile.raw_text)
    prefs = settings.load_preferences()
    title_keywords = [kw.lower() for kw in prefs.get("title_filter_keywords", [])]

    with db.connection() as conn:
        # Recheck old active scores too: otherwise upgrading the matcher leaves
        # previously auto-matched incompatible drafts in the preparation queue.
        scored = conn.execute("SELECT j.* FROM jobs j JOIN match_scores m ON j.id=m.job_id "
                              "WHERE j.status IN ('new','matched','drafted') AND j.excluded_reason IS NULL "
                              "AND m.llm_score IS NOT NULL").fetchall()
        for job in scored:
            reasons = review_reasons(profile, job, prefs)
            if reasons:
                db.save_match_score(conn, MatchScore(job_id=job["id"], embedding_similarity=0.0,
                    eligibility=classify(f"{job['title']} {job['location']} {job['description']}"),
                    llm_reasoning="Review required: " + " ".join(reasons)))
                if job["status"] in {"matched", "drafted"}:
                    pipeline.transition(conn, job["id"], "new")
        conn.commit()
        jobs = db.list_unscored_direct_ats_jobs(conn) if direct_only else db.list_jobs_without_score(conn)
        if limit:
            jobs = jobs[:limit]
        on_progress(f"Scoring {len(jobs)} unscored jobs...")

        for job in jobs:
            if title_keywords and not any(kw in job["title"].lower() for kw in title_keywords):
                db.save_match_score(
                    conn,
                    MatchScore(job_id=job["id"], embedding_similarity=0.0, eligibility="title-filtered"),
                )
                conn.commit()
                on_progress(f"  [{job['id']}] {job['title']} @ {job['company']} — skipped (title filter)")
                continue

            eligibility = classify(f"{job['title']} {job['location']} {job['description']}")
            reasons = review_reasons(profile, job, prefs)
            if reasons:
                db.save_match_score(conn, MatchScore(
                    job_id=job["id"], embedding_similarity=0.0, eligibility=eligibility,
                    llm_reasoning="Review required: " + " ".join(reasons)))
                # Never erase applied/interview history. An explicitly rescored
                # active draft must not remain automatically actionable either.
                if job["status"] in {"matched", "drafted"}:
                    pipeline.transition(conn, job["id"], "new")
                conn.commit()
                on_progress(f"  [{job['id']}] held for hard-constraint review")
                continue

            job_embedding = embed(job["description"] or job["title"])
            similarity = cosine_similarity(resume_embedding, job_embedding)

            llm_score, llm_reasoning = None, None
            if similarity >= EMBEDDING_SIMILARITY_FLOOR:
                try:
                    llm_score, llm_reasoning = rank_job(
                        profile, job["title"], job["company"], job["location"], job["description"]
                    )
                    if not valid_score(llm_score):
                        llm_score, llm_reasoning = None, "Ranking deferred: invalid score"
                except Exception as exc:  # preserve prior work; this job can be re-scored later
                    llm_reasoning = f"Ranking deferred: {type(exc).__name__}"

            db.save_match_score(
                conn,
                MatchScore(
                    job_id=job["id"],
                    embedding_similarity=similarity,
                    llm_score=llm_score,
                    llm_reasoning=llm_reasoning,
                    eligibility=eligibility,
                ),
            )

            if llm_score is not None and llm_score >= LLM_SCORE_THRESHOLD:
                pipeline.transition(conn, job["id"], "matched")

            # Matching can involve hundreds of API/model calls. Commit each completed row so
            # an intermittent provider failure never rolls back the entire direct-source batch.
            conn.commit()

            on_progress(
                f"  [{job['id']}] {job['title']} @ {job['company']} — "
                f"sim={similarity:.2f} llm_score={llm_score} eligibility={eligibility}"
            )


def slugify(text: str) -> str:
    import re

    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "job"


def draft_job(conn, job, profile, on_progress: ProgressFn = print) -> None:
    """Generate all application materials for one job (cover letter + resume tailoring
    + PDFs + gap analysis), write them to output/, and mark the job 'drafted'.
    Shared by the CLI, the API, and the scheduled prepare run."""
    from jobagent.drafting.cover_letter import build_cover_letter_pdf, draft_cover_letter
    from jobagent.drafting.gap_analysis import analyze_gaps
    from jobagent.drafting.resume_builder import (
        build_tailored_resume,
        build_tailored_resume_pdf,
        parse_tailoring_notes,
    )
    from jobagent.drafting.resume_tailor import draft_resume_tailoring

    if job["source"].split(":", 1)[0] in {"greenhouse", "lever", "ashby"}:
        link = check_live_job_link(job["url"])
        db.record_link_check(conn, job["id"], link.available)
        if link.available is False:
            db.set_excluded(conn, job["id"], link.reason or "Direct role unavailable")
            raise ValueError(link.reason or "Direct role unavailable")

    cover_letter = draft_cover_letter(profile, job["title"], job["company"], job["description"])
    resume_notes = draft_resume_tailoring(profile, job["title"], job["company"], job["description"])
    gaps = analyze_gaps(profile, job["title"], job["company"], job["location"], job["description"])

    out_dir = settings.output_dir / slugify(f"{job['company']}-{job['title']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cover_letter.md").write_text(cover_letter)
    (out_dir / "resume_tailoring.md").write_text(resume_notes)
    (out_dir / "gap_analysis.md").write_text(gaps)
    build_cover_letter_pdf(cover_letter, out_dir / "cover_letter.pdf")

    summary, highlights = parse_tailoring_notes(resume_notes)
    if summary and highlights:
        build_tailored_resume(summary, highlights, out_dir / "tailored_resume.docx", job_title=job["title"])
        build_tailored_resume_pdf(summary, highlights, out_dir / "tailored_resume.pdf", job_title=job["title"])

    pipeline.transition(conn, job["id"], "drafted")


def run_prepare(top_n: int = 3, on_progress: ProgressFn = print) -> dict:
    """Daily hands-off prep: fetch new jobs, match them, and draft materials for the
    top N highest-scored matched jobs not already drafted. Leaves a ready-to-review
    queue in the 'drafted' column — the human still submits each application."""
    on_progress("=== prepare: fetching ===")
    run_fetch(on_progress=on_progress)
    on_progress("=== prepare: matching ===")
    run_match(on_progress=on_progress)

    profile = parse_resume()
    with db.connection() as conn:
        candidates = conn.execute(
            """
            SELECT j.*
            FROM jobs j JOIN match_scores m ON j.id = m.job_id
            WHERE j.status = 'matched' AND j.excluded_reason IS NULL
              AND m.llm_score BETWEEN 1 AND 10
            ORDER BY m.llm_score DESC, m.embedding_similarity DESC
            LIMIT ?
            """,
            (top_n,),
        ).fetchall()

        if not candidates:
            on_progress("=== prepare: no new matched jobs to draft ===")
            return {"selected": 0, "drafted": 0, "failed": 0}

        on_progress(f"=== prepare: drafting top {len(candidates)} matches ===")
        drafted, failed = 0, 0
        for job in candidates:
            try:
                draft_job(conn, job, profile, on_progress)
                drafted += 1
                on_progress(f"  drafted [{job['id']}] {job['title']} @ {job['company']}")
            except Exception as exc:  # noqa: BLE001 - one draft failing shouldn't kill the run
                failed += 1
                on_progress(f"  FAILED [{job['id']}] {job['company']}: {type(exc).__name__}")
    result = {"selected": len(candidates), "drafted": drafted, "failed": failed}
    on_progress(f"=== prepare: {'incomplete' if failed else 'done'} — {drafted} drafted, {failed} failed ===")
    if failed and not drafted:
        raise RuntimeError(f"Preparation failed for all {failed} selected jobs")
    return result
