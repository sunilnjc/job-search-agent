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

CREATE TABLE IF NOT EXISTS telegram_notifications (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    notified_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS application_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    state TEXT NOT NULL,
    ats TEXT,
    final_url TEXT,
    reason TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_application_attempts_job_created
ON application_attempts (job_id, created_at DESC);
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


def list_unscored_direct_ats_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unscored Greenhouse/Lever postings, prioritised for the application pipeline."""
    return conn.execute(
        """
        SELECT jobs.* FROM jobs
        LEFT JOIN match_scores ON jobs.id = match_scores.job_id
        WHERE match_scores.job_id IS NULL
          AND (jobs.source LIKE 'greenhouse:%' OR jobs.source LIKE 'lever:%')
        ORDER BY jobs.id DESC
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


def exclude_duplicate_aggregator_listings(conn: sqlite3.Connection) -> int:
    """Hide duplicate discovery-feed rows without ever touching direct employer postings."""
    rows = conn.execute(
        """
        SELECT id FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY lower(company), lower(title), lower(location)
                       ORDER BY id
                   ) AS duplicate_rank
            FROM jobs
            WHERE source IN ('adzuna', 'remoteok', 'weworkremotely')
              AND excluded_reason IS NULL
        ) WHERE duplicate_rank > 1
        """
    ).fetchall()
    if rows:
        conn.executemany(
            "UPDATE jobs SET excluded_reason = ? WHERE id = ?",
            [("Duplicate listing from discovery feed", int(row["id"])) for row in rows],
        )
    return len(rows)


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


def telegram_candidates(
    conn: sqlite3.Connection,
    min_score: int,
    limit: int = 5,
    only_unnotified: bool = False,
) -> list[sqlite3.Row]:
    """Return only actionable, sponsorship-compatible jobs for the private bot.

    The bot must never surface a role already excluded by the eligibility workflow or one that
    needs US/UK work authorization without explicit sponsorship/worldwide eligibility.

    Eligibility is filtered by *excluding the known-bad* rather than requiring a known-good tag.
    `apply_sponsorship_exclusions` has already removed roles that need work authorization Sunil
    doesn't have, so anything still un-excluded has passed that screen. Requiring an explicit
    'worldwide'/'sponsors' tag instead would surface almost nothing: the classifier can only set
    those when a posting spells it out, and most descriptions (Adzuna especially) are truncated
    snippets, leaving ~99% of the queue tagged 'unknown'. Explicitly sponsoring roles are still
    ranked first below.
    """
    unseen_clause = "AND telegram_notifications.job_id IS NULL" if only_unnotified else ""
    return conn.execute(
        f"""
        SELECT jobs.*, match_scores.llm_score, match_scores.llm_reasoning,
               match_scores.embedding_similarity, match_scores.eligibility
        FROM jobs
        JOIN match_scores ON jobs.id = match_scores.job_id
        LEFT JOIN telegram_notifications ON jobs.id = telegram_notifications.job_id
        WHERE jobs.excluded_reason IS NULL
          AND jobs.status IN ('drafted', 'matched')
          AND match_scores.llm_score >= ?
          AND match_scores.eligibility NOT IN ('restricted', 'no-sponsorship')
          {unseen_clause}
        ORDER BY CASE WHEN match_scores.eligibility IN ('worldwide', 'sponsors') THEN 0 ELSE 1 END,
                 CASE jobs.status WHEN 'drafted' THEN 0 ELSE 1 END,
                 match_scores.llm_score DESC,
                 match_scores.embedding_similarity DESC
        LIMIT ?
        """,
        (min_score, limit),
    ).fetchall()


def mark_telegram_notified(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        """
        INSERT INTO telegram_notifications (job_id, notified_at)
        VALUES (?, ?)
        ON CONFLICT(job_id) DO UPDATE SET notified_at = excluded.notified_at
        """,
        (job_id, now_iso()),
    )


def autopilot_candidates(
    conn: sqlite3.Connection,
    min_score: int,
    limit: int,
    include_unknown_outside_us_uk: bool = False,
    require_direct_ats: bool = True,
) -> list[sqlite3.Row]:
    """Return drafts that are safe enough to enter the application worker.

    Confirmed worldwide/sponsoring roles always qualify. Unknown roles are included only
    when the owner explicitly opts in and the recorded country/location is not US/UK.
    A prior live attempt is not repeated.
    """
    return conn.execute(
        """
        SELECT jobs.*, match_scores.llm_score, match_scores.llm_reasoning,
               match_scores.embedding_similarity, match_scores.eligibility
        FROM jobs
        JOIN match_scores ON jobs.id = match_scores.job_id
        WHERE jobs.status = 'drafted'
          AND jobs.excluded_reason IS NULL
          AND match_scores.llm_score >= ?
          AND (
              ? = 0
              OR jobs.source LIKE 'greenhouse:%'
              OR jobs.source LIKE 'lever:%'
          )
          AND (
              match_scores.eligibility IN ('worldwide', 'sponsors')
              OR (
                  ? = 1
                  AND match_scores.eligibility = 'unknown'
                  AND lower(COALESCE(jobs.country, '')) NOT IN
                      ('us', 'usa', 'united states', 'united states of america', 'uk', 'gb', 'gbr', 'united kingdom')
                  AND lower(jobs.location) NOT LIKE '%united states%'
                  AND lower(jobs.location) NOT LIKE '%u.s.%'
                  AND lower(jobs.location) NOT LIKE '%united kingdom%'
                  AND lower(jobs.location) NOT LIKE '%uk%'
              )
          )
          AND NOT EXISTS (
              SELECT 1 FROM application_attempts attempts
              WHERE attempts.job_id = jobs.id
                -- Exceptions are terminal for this exact URL. Retrying an Adzuna/RemoteOK
                -- page on every Telegram command only creates duplicate noise; a future
                -- explicit retry can create a fresh attempt after its source URL is fixed.
                AND attempts.state IN ('queued', 'preparing', 'ready_for_submission', 'submitted', 'exception', 'failed')
          )
        -- A direct employer ATS is actionable now. Aggregator listings may still be useful
        -- for discovery, but should never starve a Greenhouse/Lever form behind them.
        ORDER BY CASE WHEN jobs.source LIKE 'greenhouse:%' OR jobs.source LIKE 'lever:%' THEN 0 ELSE 1 END,
                 CASE WHEN match_scores.eligibility IN ('worldwide', 'sponsors') THEN 0 ELSE 1 END,
                 match_scores.llm_score DESC, match_scores.embedding_similarity DESC
        LIMIT ?
        """,
        (min_score, int(require_direct_ats), int(include_unknown_outside_us_uk), limit),
    ).fetchall()


def create_application_attempt(conn: sqlite3.Connection, job_id: int, state: str = "queued") -> int:
    now = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO application_attempts (job_id, state, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (job_id, state, now, now),
    )
    return int(cursor.lastrowid)


def update_application_attempt(
    conn: sqlite3.Connection,
    attempt_id: int,
    *,
    state: str,
    ats: Optional[str] = None,
    final_url: Optional[str] = None,
    reason: Optional[str] = None,
    details_json: Optional[str] = None,
    submitted: bool = False,
) -> None:
    """Update one auditable application attempt without ever changing the job status."""
    fields = ["state = ?", "updated_at = ?"]
    values: list[object] = [state, now_iso()]
    for column, value in (
        ("ats", ats),
        ("final_url", final_url),
        ("reason", reason),
        ("details_json", details_json),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            values.append(value)
    if submitted:
        fields.append("submitted_at = ?")
        values.append(now_iso())
    values.append(attempt_id)
    conn.execute(f"UPDATE application_attempts SET {', '.join(fields)} WHERE id = ?", values)


def get_application_attempt(conn: sqlite3.Connection, attempt_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM application_attempts WHERE id = ?", (attempt_id,)).fetchone()


def get_application_attempt_job(conn: sqlite3.Connection, attempt_id: int) -> Optional[sqlite3.Row]:
    """Return an attempt together with the immutable job data it is applying to."""
    return conn.execute(
        """
        SELECT application_attempts.*, jobs.title, jobs.company, jobs.description, jobs.url,
               jobs.status AS job_status
        FROM application_attempts JOIN jobs ON jobs.id = application_attempts.job_id
        WHERE application_attempts.id = ?
        """,
        (attempt_id,),
    ).fetchone()
