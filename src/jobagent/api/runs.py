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


def start_autopilot(limit: int | None = None) -> str:
    """Prepare a bounded Ready queue, then deliver final-confirmation buttons privately."""
    def work(on_progress: Callable[[str], None]) -> None:
        from jobagent.applying.autopilot import process_ready_queue
        from jobagent.config import settings

        results = process_ready_queue(limit=limit)
        if not results:
            on_progress("No Ready roles meet the strict direct-source and eligibility rule.")
            return
        for result in results:
            detail = result.reason or result.final_url or ""
            on_progress(f"{result.title} — {result.state} {detail}".strip())

        # The dashboard creates no external applications by itself. Telegram is only
        # used to give Sunil the per-application final confirmation controls.
        if settings.telegram_bot_token and settings.telegram_allowed_chat_id:
            from jobagent.telegram_bot import TelegramBot

            bot = TelegramBot()
            try:
                bot.send_autopilot_results(settings.telegram_allowed_chat_id, results)
                on_progress("Sent private Telegram confirmation/exceptions summary.")
            finally:
                bot.close()
        else:
            on_progress("Telegram is not configured; packets are ready but no confirmation buttons were sent.")

    return _start(work)


def start_direct_apply(job_id: int) -> str:
    """Prepare one requested Ready role and deliver its final step to Telegram."""
    def work(on_progress: Callable[[str], None]) -> None:
        from jobagent.applying.autopilot import process_direct_apply_job
        from jobagent.config import settings

        result = process_direct_apply_job(job_id)
        on_progress(f"{result.title} — {result.state} {result.reason or result.final_url or ''}".strip())
        if settings.telegram_bot_token and settings.telegram_allowed_chat_id:
            from jobagent.telegram_bot import TelegramBot

            bot = TelegramBot()
            try:
                bot.send_autopilot_results(settings.telegram_allowed_chat_id, [result])
                on_progress("Sent private Telegram confirmation or exception.")
            finally:
                bot.close()

    return _start(work)


def get_run(run_id: str) -> dict | None:
    return RUNS.get(run_id)
