from __future__ import annotations

import sqlite3

from jobagent.models import STATUSES
from jobagent.storage import db


def transition(conn: sqlite3.Connection, job_id: int, new_status: str) -> None:
    if new_status not in STATUSES:
        raise ValueError(f"Unknown status '{new_status}'. Valid: {', '.join(STATUSES)}")
    job = db.get_job(conn, job_id)
    if not job:
        raise ValueError(f"No job with id {job_id}")
    db.set_status(conn, job_id, new_status)


def summarize(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for row in db.list_jobs_by_status(conn):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts
