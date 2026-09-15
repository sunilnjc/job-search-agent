"""Minimal diagnostic events for founder/mobile process logs and founder RUNS.

Arbitrary prose cannot be reliably made private with token/email regexes. Drop
it instead: no prompt/resume/request body, headers, URL/query, client address,
exception message, traceback, or arbitrary logging extra survives this filter.
Only allowlisted event names, methods, statuses and exception types are retained.
This does not intercept explicit CLI output, upstream proxy logs, or third-party
telemetry. Reinstall after any later logging reconfiguration/addition of handlers.
"""
from __future__ import annotations

import logging
import re


MAX_RUN_LOG_ENTRIES = 200
_FILTER_TOKEN = object()
_EXCEPTION_TYPES = frozenset({
    "Exception", "RuntimeError", "ValueError", "TypeError", "KeyError", "IndexError",
    "TimeoutError", "ConnectionError", "PermissionError", "FileNotFoundError", "OSError",
    "HTTPException", "HTTPStatusError", "RequestError", "ConnectError", "ReadTimeout",
    "ConnectTimeout", "WriteTimeout", "PoolTimeout", "RemoteProtocolError", "APIError",
    "APIConnectionError", "APITimeoutError", "RateLimitError", "AuthenticationError",
    "ValidationError", "StudioError", "ProviderError", "GroundingError", "PDFSafetyError",
    "PdfReadError", "PdfStreamError", "RecursionError", "MemoryError", "CancelledError",
})
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"})
_EVENTS = {
    "Telegram-triggered drafting failed for job %s": "drafting_failed",
    "Failed to handle Telegram update": "telegram_update_failed",
    "Telegram polling failed; retrying shortly": "telegram_poll_failed",
    "Telegram bot started; waiting for updates": "telegram_started",
}
_PHASES = {
    "=== prepare: fetching ===": "phase=fetch",
    "=== prepare: matching ===": "phase=match",
    "=== prepare: no new matched jobs to draft ===": "phase=prepare_empty",
}
_COUNT_EVENTS = (
    (re.compile(r"Scoring ([0-9]{1,9}) unscored jobs\.\.\.\Z"), "scoring"),
    (re.compile(r"Done\. ([0-9]{1,9}) postings processed\.\Z"), "fetch_complete"),
    (re.compile(r"=== prepare: drafting top ([0-9]{1,9}) matches ===\Z"), "drafting"),
)


def safe_exception_type(value) -> str:
    kind = value if isinstance(value, type) else type(value)
    name = getattr(kind, "__name__", "Exception")
    return name if name in _EXCEPTION_TYPES else "Exception"


def safe_exception_event(exc: BaseException) -> str:
    return "ERROR: " + safe_exception_type(exc)


def safe_progress_event(message) -> str:
    if not isinstance(message, str) or len(message) > 1024:
        return "event=progress_update"
    if message in _PHASES:
        return _PHASES[message]
    for pattern, event in _COUNT_EVENTS:
        match = pattern.fullmatch(message)
        if match:
            return f"event={event} count={int(match[1])}"
    match = re.fullmatch(r"=== prepare: (done|incomplete) — ([0-9]{1,9}) drafted, ([0-9]{1,9}) failed ===", message)
    if match:
        return f"event=prepare_{match[1]} drafted={int(match[2])} failed={int(match[3])}"
    return "event=progress_update"


def append_run_event(log: list[str], event: str) -> None:
    """Call with events produced by the safe_* helpers, never upstream text."""
    log.append(event)
    del log[:-MAX_RUN_LOG_ENTRIES]


class PrivacyFilter(logging.Filter):
    """In-place redaction before emit/format, including cached tracebacks/extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.__dict__.get("_jobagent_private") is _FILTER_TOKEN:
            return True
        name = record.name if isinstance(record.name, str) else ""
        bucket = next((prefix for prefix in ("jobagent", "uvicorn", "httpx", "httpcore", "openai", "anthropic", "pypdf")
                       if name == prefix or name.startswith(prefix + ".")), "application")
        event = "diagnostic"
        if bucket in {"httpx", "httpcore"}:
            event = "http_client"
        elif bucket in {"openai", "anthropic"}:
            event = "provider_diagnostic"
        elif bucket == "pypdf":
            event = "pdf_diagnostic"
        elif isinstance(record.msg, str) and record.msg in _EVENTS:
            event = _EVENTS[record.msg]
        message = "event=" + event
        if name == "uvicorn.access":
            # Uvicorn normally formats client/method/full_path/version/status.
            # Preserve only method/status; no URL path, query, peer IP or headers.
            message = "event=http_request"
            args = record.args
            if isinstance(args, tuple) and len(args) == 5:
                method, status = args[1], args[4]
                if isinstance(method, str) and method in _METHODS:
                    message += " method=" + method
                if type(status) is int and 100 <= status <= 599:
                    message += f" status={status}"
        if record.exc_info:
            exception = record.exc_info[0] if isinstance(record.exc_info, tuple) and record.exc_info else Exception
            message += " exception_type=" + safe_exception_type(exception)
        # Do not getMessage(), format exceptions, or stringify arbitrary objects.
        # Remove all extra fields, including JSON logger headers/body/prompts.
        keep = {"created", "msecs", "relativeCreated", "levelno", "lineno", "process", "thread"}
        for key in list(record.__dict__):
            if key not in keep:
                del record.__dict__[key]
        record.name = bucket
        record.levelname = {10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL"}.get(record.levelno, "LOG")
        record.msg, record.args = message, ()
        record.message = message
        record.pathname = record.filename = record.module = record.funcName = "[withheld]"
        record.processName = record.threadName = "[withheld]"
        record.exc_info = record.exc_text = record.stack_info = None
        record._jobagent_private = _FILTER_TOKEN
        return True


class PrivacyFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(levelname)s %(name)s %(message)s")
        self.privacy_filter = PrivacyFilter()

    def format(self, record):
        self.privacy_filter.filter(record)
        return super().format(record)


def install_privacy_logging() -> None:
    """Secure existing handlers (including Uvicorn access) at process startup.

    Uvicorn's AccessFormatter needs the original raw 5-tuple; replacing it with
    our formatter avoids keeping that sensitive tuple solely to satisfy it.
    No levels, propagation, worker behavior, credentials or business logic change.
    """
    loggers = [logging.getLogger()] + [value for value in logging.root.manager.loggerDict.copy().values()
                                     if isinstance(value, logging.Logger)]
    if not logging.getLogger().handlers:
        logging.getLogger().addHandler(logging.StreamHandler())
    handlers = {handler for logger in loggers for handler in logger.handlers}
    if logging.lastResort is not None:
        handlers.add(logging.lastResort)
    for handler in handlers:
        if not any(isinstance(item, PrivacyFilter) for item in handler.filters):
            handler.filters.insert(0, PrivacyFilter())
        handler.setFormatter(PrivacyFormatter())
