from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from jobagent.config import settings
from jobagent.models import JobPosting, MatchScore, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    remote INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    salary TEXT,
    posted_at TEXT,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    excluded_reason TEXT,
    country TEXT
);

CREATE TABLE IF NOT EXISTS match_scores (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    embedding_similarity REAL NOT NULL,
    llm_score INTEGER,
    llm_reasoning TEXT,
    eligibility TEXT NOT NULL DEFAULT 'unknown',
    scored_at TEXT NOT NULL
);
"""


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or settings.db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets readers (e.g. the web UI polling /api/jobs) keep working while a
    # long-running match/fetch run holds a write transaction open for minutes.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Optional[Path] = None) -> None:
    with connection(db_path) as conn:
        conn.executescript(SCHEMA)
        # Migration for DBs created before the eligibility column existed.
        try:
            conn.execute("ALTER TABLE match_scores ADD COLUMN eligibility TEXT NOT NULL DEFAULT 'unknown'")
        except sqlite3.OperationalError:
            pass
        # Migration for DBs created before the excluded_reason column existed.
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN excluded_reason TEXT")
        except sqlite3.OperationalError:
            pass
        # Migration for DBs created before the country column existed.
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN country TEXT")
        except sqlite3.OperationalError:
            pass


def upsert_job(conn: sqlite3.Connection, job: JobPosting) -> int:
    job.fetched_at = job.fetched_at or now_iso()
    cur = conn.execute("SELECT id FROM jobs WHERE url = ?", (job.url,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO jobs (source, external_id, title, company, location, remote, url,
                           description, salary, posted_at, fetched_at, status, country)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
        """,
        (
            job.source,
            job.external_id,
            job.title,
            job.company,
            job.location,
            int(job.remote),
            job.url,
            job.description,
            job.salary,
            job.posted_at,
            job.fetched_at,
            job.country,
        ),
    )
    return cur.lastrowid


def get_job(conn: sqlite3.Connection, job_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs_without_score(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT jobs.* FROM jobs
        LEFT JOIN match_scores ON jobs.id = match_scores.job_id
        WHERE match_scores.job_id IS NULL
        """
    ).fetchall()


def save_match_score(conn: sqlite3.Connection, score: MatchScore) -> None:
    score.scored_at = score.scored_at or now_iso()
    conn.execute(
        """
        INSERT INTO match_scores (job_id, embedding_similarity, llm_score, llm_reasoning, eligibility, scored_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            embedding_similarity = excluded.embedding_similarity,
            llm_score = excluded.llm_score,
            llm_reasoning = excluded.llm_reasoning,
            eligibility = excluded.eligibility,
            scored_at = excluded.scored_at
        """,
        (score.job_id, score.embedding_similarity, score.llm_score, score.llm_reasoning, score.eligibility, score.scored_at),
    )


def top_ranked_jobs(conn: sqlite3.Connection, limit: int = 10, only_status: str = "matched") -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT jobs.*, match_scores.llm_score, match_scores.llm_reasoning,
               match_scores.embedding_similarity, match_scores.eligibility
        FROM jobs
        JOIN match_scores ON jobs.id = match_scores.job_id
        WHERE jobs.status = ?
        ORDER BY match_scores.llm_score DESC, match_scores.embedding_similarity DESC
        LIMIT ?
        """,
        (only_status, limit),
    ).fetchall()


def set_status(conn: sqlite3.Connection, job_id: int, status: str) -> None:
    conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))


def set_excluded(conn: sqlite3.Connection, job_id: int, reason: Optional[str]) -> None:
    """Set (or clear, with reason=None) a job's excluded_reason. Excluded jobs are hidden
    from the kanban board but remain in the DB, visible in the Excluded tab."""
    conn.execute("UPDATE jobs SET excluded_reason = ? WHERE id = ?", (reason, job_id))


def list_jobs_by_status(conn: sqlite3.Connection, status: Optional[str] = None) -> list[sqlite3.Row]:
    if status:
        return conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY id", (status,)).fetchall()
    return conn.execute("SELECT * FROM jobs ORDER BY status, id").fetchall()


def list_jobs_with_scores(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All jobs with their match score/eligibility if scored, for the kanban board."""
    return conn.execute(
        """
        SELECT jobs.*, match_scores.llm_score, match_scores.llm_reasoning,
               match_scores.embedding_similarity, match_scores.eligibility
        FROM jobs
        LEFT JOIN match_scores ON jobs.id = match_scores.job_id
        ORDER BY match_scores.llm_score DESC, jobs.id DESC
        """
    ).fetchall()
