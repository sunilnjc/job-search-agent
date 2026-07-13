from __future__ import annotations

import threading
import uuid
from typing import Callable

from jobagent.service import run_fetch, run_match

RUNS: dict[str, dict] = {}
# {run_id: {"status": "running"|"done"|"error", "log": [str, ...]}}


def _start(target: Callable[[Callable[[str], None]], None]) -> str:
    run_id = str(uuid.uuid4())
    RUNS[run_id] = {"status": "running", "log": []}

    def on_progress(msg: str) -> None:
        RUNS[run_id]["log"].append(msg)

    def worker() -> None:
        try:
            target(on_progress)
            RUNS[run_id]["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - surface any failure to the poller
            RUNS[run_id]["log"].append(f"ERROR: {exc}")
            RUNS[run_id]["status"] = "error"

    threading.Thread(target=worker, daemon=True).start()
    return run_id


def start_fetch(url: str | None = None) -> str:
    return _start(lambda on_progress: run_fetch(url, on_progress=on_progress))


def start_match(limit: int | None = None) -> str:
    return _start(lambda on_progress: run_match(limit, on_progress=on_progress))


def get_run(run_id: str) -> dict | None:
    return RUNS.get(run_id)
