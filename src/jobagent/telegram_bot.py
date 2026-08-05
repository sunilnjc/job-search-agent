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
    "/autopilot — prepare strictly eligible Ready applications\n"
    "/status — pipeline counts\n\n"
    "<b>📝 Prepare application</b> writes the cover letter and tailored resume for a role, "
    "then sends them here; <b>📄 Send documents</b> re-sends them once they exist. "
    "<b>Review packet</b> opens the private phone dashboard.\n\n"
    "The bot never submits anything — <b>✓ Mark applied</b> only records an application "
    "after you have submitted it yourself on the company site."
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
    # Offer whichever action is actually possible: send the PDFs if they exist, otherwise
    # offer to write them. Without this second button a promising matched-but-undrafted
    # role is a dead end in Telegram — you'd have to go to a laptop to draft it.
    if job_document_paths(row):
        rows.append([{"text": "📄 Send documents", "callback_data": f"docs:{job_id}"}])
    else:
        rows.append([{"text": "📝 Prepare application", "callback_data": f"draft:{job_id}"}])
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

    def prepare_job_documents(self, conn, chat_id: str | int, job: Mapping[str, Any]) -> bool:
        """Write the cover letter, tailored resume and gap analysis, then send the PDFs.

        Drafting is several LLM calls and takes up to a minute, which blocks this
        single-user bot's poll loop — acceptable here, and better than the alternative of
        a promising role being un-actionable until you reach a laptop.
        """
        from jobagent.profile.resume_parser import parse_resume
        from jobagent.service import draft_job

        title = html.escape(_short(job["title"], 100))
        self.send_text(chat_id, f"📝 Preparing your application for <b>{title}</b>. This takes a minute…")
        try:
            draft_job(conn, job, parse_resume(), on_progress=lambda _: None)
        except Exception:  # noqa: BLE001 - report the failure rather than going silent
            logger.exception("Telegram-triggered drafting failed for job %s", job["id"])
            self.send_text(chat_id, f"Could not prepare <b>{title}</b>. Try again from the dashboard.")
            return False

        sent = self.send_job_documents(chat_id, job)
        if sent:
            self.send_text(
                chat_id,
                f"✅ <b>{title}</b> is ready — review both documents before you submit. "
                "Then use <b>✓ Mark applied</b> once you've sent it on the company site.",
            )
        else:
            self.send_text(chat_id, f"Prepared <b>{title}</b>, but no PDFs were produced.")
        return bool(sent)

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

    def run_autopilot(self, chat_id: str | int) -> int:
        """Prepare the strict Ready queue and describe every safe stopping point."""
        from jobagent.applying.autopilot import process_ready_queue

        results = process_ready_queue()
        if not results:
            eligibility_note = (
                "including unknown roles outside the US/UK" if settings.autopilot_include_unknown_outside_us_uk
                else "with explicit worldwide or sponsorship eligibility"
            )
            self.send_text(
                chat_id,
                f"No drafted roles currently meet the autopilot rule: score 9+ and {eligibility_note}.",
            )
            return 0
        lines = ["<b>Application preparation</b>"]
        buttons: list[list[dict[str, str]]] = []
        for result in results:
            role = html.escape(_short(f"{result.title} — {result.company}", 120))
            if result.state == "ready_for_submission":
                lines.append(f"• {role}: packet ready; choose Confirm submit to send it.")
                buttons.append([{"text": f"Confirm submit: {_short(result.company, 28)}", "callback_data": f"submit:{result.attempt_id}"}])
            else:
                reason = html.escape((result.reason or "needs review").replace("_", " "))
                lines.append(f"• {role}: <b>action required</b> ({reason}).")
        self.send_text(chat_id, "\n".join(lines), {"inline_keyboard": buttons} if buttons else None)
        return len(results)

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
        elif command == "/autopilot":
            self.run_autopilot(chat_id)
        else:
            self.send_text(chat_id, "Use /today, /matches, /autopilot, /status, or /help.")

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

        if action == "submit":
            from jobagent.applying.autopilot import submit_attempt

            self.answer_callback(callback_id, "Submitting the prepared application…")
            try:
                result = submit_attempt(job_id)
            except ValueError:
                self.send_text(chat_id, "That application attempt no longer exists.")
                return
            role = html.escape(_short(f"{result.title} — {result.company}", 120))
            if result.state == "submitted":
                self.send_text(chat_id, f"✅ Submitted <b>{role}</b> and marked it Applied.")
            else:
                reason = html.escape((result.reason or "needs review").replace("_", " "))
                self.send_text(chat_id, f"⚠️ <b>{role}</b> was not submitted: {reason}.")
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
            if action == "draft":
                self.answer_callback(callback_id, "Writing your application…")
                self.prepare_job_documents(conn, chat_id, job)
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
