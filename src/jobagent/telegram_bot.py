"""Private Telegram companion for reviewing Job Search Agent matches on a phone.

This module deliberately uses Telegram's plain HTTP Bot API through the project's existing
httpx dependency. There is no public webhook: long polling keeps the bot on Sunil's Mac and
the chat-ID allowlist rejects every other user.
"""
from __future__ import annotations

import html
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from jobagent.config import settings
from jobagent.storage import db
from jobagent.tracking import pipeline

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>Job Search Agent</b>\n\n"
    "/today — eligible high-match roles ready to review\n"
    "/matches — same as /today\n"
    "/status — pipeline counts\n\n"
    "Use <b>Review packet</b> to open the private phone dashboard, or "
    "<b>📄 Send documents</b> to get the cover letter and tailored resume right here. "
    "<b>Mark applied</b> only records an application after you submit it on the company site."
)


def _short(text: Any, limit: int = 420) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


# Documents the drafting pipeline writes per job, in the order they're most useful to read.
JOB_DOCUMENTS = (
    ("cover_letter.pdf", "Cover letter"),
    ("tailored_resume.pdf", "Tailored resume"),
)


def job_document_paths(row: Mapping[str, Any]) -> list[tuple[Path, str]]:
    """Existing application PDFs for a job, so they can be sent straight into the chat."""
    from jobagent.service import slugify  # local import: service pulls in the whole pipeline

    folder = settings.output_dir / slugify(f"{row['company']}-{row['title']}")
    return [(folder / name, label) for name, label in JOB_DOCUMENTS if (folder / name).is_file()]


def build_job_card(row: Mapping[str, Any], dashboard_url: str) -> tuple[str, dict[str, Any]]:
    """Build an HTML-safe Telegram message and inline buttons for one job."""
    score = row.get("llm_score") if hasattr(row, "get") else row["llm_score"]
    title = html.escape(_short(row["title"], 120))
    company = html.escape(_short(row["company"], 80))
    location = html.escape(_short(row["location"], 80))
    eligibility = str(row["eligibility"] or "unknown").replace("-", " ").upper()
    status = str(row["status"]).upper()
    reasoning = html.escape(_short(row.get("llm_reasoning") if hasattr(row, "get") else row["llm_reasoning"]))

    message = (
        f"<b>{title}</b>\n"
        f"{company} · {location}\n"
        f"<b>{score}/10</b> · {html.escape(eligibility)} · {html.escape(status)}"
    )
    if reasoning:
        message += f"\n\n{reasoning}"

    job_id = int(row["id"])
    first_row: list[dict[str, str]] = []
    if dashboard_url:
        first_row.append({"text": "Review packet", "url": f"{dashboard_url}?job={job_id}"})
    first_row.append({"text": "Open application ↗", "url": str(row["url"])})
    rows: list[list[dict[str, str]]] = [
        first_row,
        [
            {"text": "✓ Mark applied", "callback_data": f"applied:{job_id}"},
            {"text": "Not a fit", "callback_data": f"exclude:{job_id}"},
        ],
    ]
    # Only offer documents when they actually exist — a button that always errors is worse
    # than no button. Matched-but-undrafted jobs have no PDFs yet.
    if job_document_paths(row):
        rows.append([{"text": "📄 Send documents", "callback_data": f"docs:{job_id}"}])
    return message, {"inline_keyboard": rows}


class TelegramBot:
    def __init__(
        self,
        token: str | None = None,
        allowed_chat_id: str | None = None,
        dashboard_url: str | None = None,
        min_score: int | None = None,
    ) -> None:
        self.token = token if token is not None else settings.telegram_bot_token
        self.allowed_chat_id = str(allowed_chat_id if allowed_chat_id is not None else settings.telegram_allowed_chat_id)
        self.dashboard_url = dashboard_url if dashboard_url is not None else settings.telegram_dashboard_url
        self.min_score = min_score if min_score is not None else settings.telegram_min_score
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
        self.client = httpx.Client(timeout=httpx.Timeout(35.0, connect=10.0))

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        response = self.client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload or {})
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {body.get('description', 'unknown error')}")
        return body.get("result")

    def _is_allowed(self, chat_id: Any) -> bool:
        return bool(self.allowed_chat_id) and str(chat_id) == self.allowed_chat_id

    def send_text(self, chat_id: str | int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._request("sendMessage", payload)

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._request("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def send_document(self, chat_id: str | int, path: Path, caption: str = "") -> None:
        """Upload a local PDF into the chat (multipart, so it can't use _request)."""
        data: dict[str, str] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
        with path.open("rb") as handle:
            response = self.client.post(
                f"https://api.telegram.org/bot{self.token}/sendDocument",
                data=data,
                files={"document": (path.name, handle, "application/pdf")},
            )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram sendDocument failed: {body.get('description', 'unknown error')}")

    def send_job_documents(self, chat_id: str | int, job: Mapping[str, Any]) -> int:
        """Send this job's application PDFs so they can be reviewed and submitted from the phone."""
        documents = job_document_paths(job)
        for path, label in documents:
            self.send_document(chat_id, path, caption=f"{label} — {_short(job['title'], 80)}")
        return len(documents)

    def send_matches(self, chat_id: str | int, only_unnotified: bool = False, limit: int = 5) -> int:
        with db.connection() as conn:
            jobs = db.telegram_candidates(
                conn,
                min_score=self.min_score,
                limit=limit,
                only_unnotified=only_unnotified,
            )
            if not jobs:
                if not only_unnotified:
                    self.send_text(chat_id, "No new eligible high-match roles are ready right now.")
                return 0

            for job in jobs:
                message, keyboard = build_job_card(job, self.dashboard_url)
                self.send_text(chat_id, message, keyboard)
                if only_unnotified:
                    db.mark_telegram_notified(conn, int(job["id"]))
        return len(jobs)

    def send_status(self, chat_id: str | int) -> None:
        with db.connection() as conn:
            counts = pipeline.summarize(conn)
        lines = [f"<b>{html.escape(status.title())}</b>: {count}" for status, count in counts.items() if count]
        self.send_text(chat_id, "<b>Pipeline status</b>\n\n" + "\n".join(lines))

    def handle_message(self, message: Mapping[str, Any]) -> None:
        chat_id = message.get("chat", {}).get("id")
        text = str(message.get("text") or "").strip()
        if not text:
            return  # stickers/photos/etc. carry no command — ignore rather than crash
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()

        if not self.allowed_chat_id:
            if command in {"/start", "/whoami"}:
                self.send_text(
                    chat_id,
                    "<b>Private Job Search Agent bot</b>\n\n"
                    f"Your chat ID is <code>{html.escape(str(chat_id))}</code>. Add it locally as "
                    "<code>TELEGRAM_ALLOWED_CHAT_ID</code> in .env, then restart the bot. "
                    "Until then, no job data is available.",
                )
            return

        if not self._is_allowed(chat_id):
            self.send_text(chat_id, "This is a private bot.")
            return

        if command in {"/start", "/help"}:
            self.send_text(chat_id, HELP_TEXT)
        elif command in {"/today", "/matches"}:
            self.send_matches(chat_id)
        elif command == "/status":
            self.send_status(chat_id)
        else:
            self.send_text(chat_id, "Use /today, /matches, /status, or /help.")

    def handle_callback(self, callback: Mapping[str, Any]) -> None:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        callback_id = str(callback.get("id") or "")
        if not self._is_allowed(chat_id):
            if callback_id:
                self.answer_callback(callback_id, "This is a private bot.")
            return

        try:
            action, raw_job_id = str(callback.get("data") or "").split(":", 1)
            job_id = int(raw_job_id)
        except (ValueError, TypeError):
            self.answer_callback(callback_id, "Invalid action.")
            return

        with db.connection() as conn:
            job = db.get_job(conn, job_id)
            if not job:
                self.answer_callback(callback_id, "That job no longer exists.")
                return
            if action == "docs":
                # Sending happens outside the DB transaction below; do it here and return.
                sent = self.send_job_documents(chat_id, job)
                self.answer_callback(
                    callback_id,
                    f"Sent {sent} document(s)." if sent else "No documents drafted for this job yet.",
                )
                return
            if action == "applied":
                pipeline.transition(conn, job_id, "applied")
                response = "Marked as applied."
                follow_up = f"✓ Recorded <b>{html.escape(_short(job['title'], 100))}</b> as applied."
            elif action == "exclude":
                db.set_excluded(conn, job_id, "Excluded via private Telegram bot")
                response = "Removed from your queue."
                follow_up = f"Removed <b>{html.escape(_short(job['title'], 100))}</b> from your queue."
            else:
                self.answer_callback(callback_id, "Invalid action.")
                return

        self.answer_callback(callback_id, response)
        self.send_text(chat_id, follow_up)

    def handle_update(self, update: Mapping[str, Any]) -> None:
        if "message" in update:
            self.handle_message(update["message"])
        elif "callback_query" in update:
            self.handle_callback(update["callback_query"])

    def run_forever(self) -> None:
        """Long-poll Telegram so no inbound public port or webhook is needed."""
        db.init_db()
        offset: int | None = None
        logger.info("Telegram bot started; waiting for updates")
        try:
            while True:
                payload: dict[str, Any] = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
                if offset is not None:
                    payload["offset"] = offset
                try:
                    updates = self._request("getUpdates", payload) or []
                    for update in updates:
                        offset = int(update["update_id"]) + 1
                        try:
                            self.handle_update(update)
                        except Exception:  # noqa: BLE001 - one malformed update must not stop the bot
                            logger.exception("Failed to handle Telegram update")
                except Exception:  # noqa: BLE001 - recover from a transient network/API error
                    logger.exception("Telegram polling failed; retrying shortly")
                    time.sleep(3)
        finally:
            self.close()

    def notify_new_matches(self, limit: int = 5) -> int:
        if not self.allowed_chat_id:
            raise RuntimeError("TELEGRAM_ALLOWED_CHAT_ID is not set in .env")
        try:
            return self.send_matches(self.allowed_chat_id, only_unnotified=True, limit=limit)
        finally:
            self.close()
