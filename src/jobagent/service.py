from __future__ import annotations

from typing import Callable, Optional

from jobagent.config import settings
from jobagent.matching.eligibility import classify
from jobagent.matching.embeddings import cosine_similarity, embed
from jobagent.matching.ollama_rank import rank_job
from jobagent.models import MatchScore
from jobagent.profile.resume_parser import parse_resume
from jobagent.sources.adzuna import AdzunaSource
from jobagent.sources.ats_boards import ATSBoardsSource
from jobagent.sources.manual_url import ManualURLSource
from jobagent.sources.remoteok import RemoteOKSource
from jobagent.sources.weworkremotely import WeWorkRemotelySource
from jobagent.storage import db
from jobagent.tracking import pipeline

BULK_SOURCES = [RemoteOKSource(), WeWorkRemotelySource(), AdzunaSource(), ATSBoardsSource()]

LLM_SCORE_THRESHOLD = 6
EMBEDDING_SIMILARITY_FLOOR = 0.2

ProgressFn = Callable[[str], None]


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
        for source in BULK_SOURCES:
            try:
                postings = source.search()
            except Exception as exc:  # noqa: BLE001 - one source failing shouldn't kill the run
                on_progress(f"[{source.name}] failed: {exc}")
                continue
            for posting in postings:
                db.upsert_job(conn, posting)
            on_progress(f"[{source.name}] fetched {len(postings)} postings")
            total += len(postings)
    on_progress(f"Done. {total} postings processed.")
    return total


def run_match(limit: Optional[int] = None, on_progress: ProgressFn = print) -> None:
    """Score fetched jobs against the resume: title filter + eligibility + embedding + LLM rating."""
    db.init_db()
    profile = parse_resume()
    resume_embedding = embed(profile.raw_text)
    title_keywords = [
        kw.lower() for kw in settings.load_preferences().get("title_filter_keywords", [])
    ]

    with db.connection() as conn:
        jobs = db.list_jobs_without_score(conn)
        if limit:
            jobs = jobs[:limit]
        on_progress(f"Scoring {len(jobs)} unscored jobs...")

        for job in jobs:
            if title_keywords and not any(kw in job["title"].lower() for kw in title_keywords):
                db.save_match_score(
                    conn,
                    MatchScore(job_id=job["id"], embedding_similarity=0.0, eligibility="title-filtered"),
                )
                on_progress(f"  [{job['id']}] {job['title']} @ {job['company']} — skipped (title filter)")
                continue

            eligibility = classify(f"{job['title']} {job['location']} {job['description']}")

            # Restricted roles (needs local work authorization we don't have) skip
            # LLM ranking entirely — they can't be accepted regardless of fit.
            if eligibility == "restricted":
                db.save_match_score(
                    conn,
                    MatchScore(job_id=job["id"], embedding_similarity=0.0, eligibility=eligibility),
                )
                on_progress(f"  [{job['id']}] {job['title']} @ {job['company']} — skipped (restricted)")
                continue

            job_embedding = embed(job["description"] or job["title"])
            similarity = cosine_similarity(resume_embedding, job_embedding)

            llm_score, llm_reasoning = None, None
            if similarity >= EMBEDDING_SIMILARITY_FLOOR:
                llm_score, llm_reasoning = rank_job(
                    profile, job["title"], job["company"], job["location"], job["description"]
                )

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

            on_progress(
                f"  [{job['id']}] {job['title']} @ {job['company']} — "
                f"sim={similarity:.2f} llm_score={llm_score} eligibility={eligibility}"
            )


def slugify(text: str) -> str:
    import re

    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "job"
