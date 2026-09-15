"""JP033: capture synthetic sentinels only; no owner logs, .env, or workers."""
from __future__ import annotations

import io
import logging
import os
import socket
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from jobagent.privacy_logging import (
    MAX_RUN_LOG_ENTRIES, PrivacyFilter, PrivacyFormatter, append_run_event,
    install_privacy_logging, safe_exception_event, safe_progress_event,
)


PRIVATE = (
    "Bearer synthetic-private-bearer sk-proj-SYNTHETICPRIVATEKEY "
    "person-sentinel@example.test +44 7700 900123 "
    "https://example.test/auth/callback?access_token=QUERY_SENTINEL#refresh_token=FRAGMENT_SENTINEL "
    "PROMPT_SENTINEL career resume salary PRIVATE_FREE_PROSE"
)


class ExplodingText:
    def __str__(self):
        raise AssertionError("Never stringify unknown log objects")

    __repr__ = __str__


class PrivacyLoggingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("connect", "connect_ex", "sendto"):
            self.stack.enter_context(patch.object(socket.socket, name, side_effect=AssertionError("No network")))
        self.stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=AssertionError("No DNS")))

    def record(self, name="jobagent.synthetic", message=PRIVATE, args=(), exception=None):
        record = logging.LogRecord(name, logging.ERROR, "/private/synthetic/source.py", 17,
                                   message, args, exception, func=PRIVATE, sinfo=PRIVATE)
        record.exc_text = PRIVATE
        record.message = PRIVATE
        record.headers = {"Authorization": PRIVATE}
        record.body = {"prompt": PRIVATE}
        record.url = PRIVATE
        record.color_message = PRIVATE
        return record

    def assert_private(self, text):
        for sentinel in ("synthetic-private-bearer", "SYNTHETICPRIVATEKEY", "person-sentinel", "7700",
                         "QUERY_SENTINEL", "FRAGMENT_SENTINEL", "PROMPT_SENTINEL", "PRIVATE_FREE_PROSE"):
            self.assertNotIn(sentinel, text)

    def test_filter_removes_message_args_extras_cached_exception_and_stack(self):
        record = self.record(message="Upstream failed: %s", args=(PRIVATE,))
        PrivacyFilter().filter(record)
        self.assert_private(str(record.__dict__))
        self.assertEqual(record.args, ())
        self.assertIsNone(record.exc_info)
        self.assertIsNone(record.exc_text)
        self.assertIsNone(record.stack_info)
        self.assertNotIn("headers", record.__dict__)
        self.assertEqual(record.getMessage(), "event=diagnostic")

    def test_chained_exception_keeps_only_safe_exception_type(self):
        try:
            try:
                raise ValueError(PRIVATE)
            except ValueError as exc:
                raise RuntimeError(PRIVATE) from exc
        except RuntimeError:
            record = self.record(exception=sys.exc_info())
        output = PrivacyFormatter().format(record)
        self.assertEqual(output, "ERROR jobagent event=diagnostic exception_type=RuntimeError")
        self.assert_private(output)
        self.assertNotIn("Traceback", output)
        self.assertIsNone(record.exc_info)

    def test_unknown_exception_class_name_not_logged(self):
        private_type = type("PROMPT_SENTINEL", (Exception,), {})
        self.assertEqual(safe_exception_event(private_type(PRIVATE)), "ERROR: Exception")
        self.assertEqual(safe_exception_event(ValueError(PRIVATE)), "ERROR: ValueError")

    def test_private_logger_name_and_arbitrary_objects_are_not_stringified(self):
        record = self.record(name=PRIVATE, message=ExplodingText(), args=(ExplodingText(),))
        output = PrivacyFormatter().format(record)
        self.assertEqual(output, "ERROR application event=diagnostic")
        self.assert_private(str(record.__dict__))

    def test_access_log_keeps_method_status_not_client_path_query_or_auth(self):
        record = self.record(name="uvicorn.access", message='%s - "%s %s HTTP/%s" %d',
                             args=(PRIVATE, "POST", "/api/mobile/chat?token=" + PRIVATE, "1.1", 422))
        output = PrivacyFormatter().format(record)
        self.assertEqual(output, "ERROR uvicorn event=http_request method=POST status=422")
        self.assert_private(str(record.__dict__))

    def test_http_provider_and_pdf_logs_withhold_all_prose(self):
        for name, event in (("httpx", "http_client"), ("httpcore.connection", "http_client"),
                            ("openai._base_client", "provider_diagnostic"),
                            ("anthropic._base_client", "provider_diagnostic"), ("pypdf._reader", "pdf_diagnostic")):
            with self.subTest(name=name):
                output = PrivacyFormatter().format(self.record(name=name))
                self.assertIn("event=" + event, output)
                self.assert_private(output)

    def test_known_failure_event_retains_diagnostic_not_private_args(self):
        record = self.record(message="Telegram-triggered drafting failed for job %s", args=(PRIVATE,))
        self.assertEqual(PrivacyFormatter().format(record), "ERROR jobagent event=drafting_failed")

    def test_boolean_extra_cannot_spoof_already_redacted_marker(self):
        record = self.record()
        record._jobagent_private = True
        self.assert_private(PrivacyFormatter().format(record))

    def test_progress_allowlists_phases_counts_and_never_retains_raw_prose(self):
        self.assertEqual(safe_progress_event("=== prepare: fetching ==="), "phase=fetch")
        self.assertEqual(safe_progress_event("Scoring 12 unscored jobs..."), "event=scoring count=12")
        self.assertEqual(safe_progress_event("=== prepare: incomplete — 2 drafted, 1 failed ==="),
                         "event=prepare_incomplete drafted=2 failed=1")
        for value in (PRIVATE, "Fetched 1 job from " + PRIVATE, "Done. 3 postings processed. " + PRIVATE,
                      "Scoring 123456789123456789 unscored jobs...", ExplodingText()):
            self.assertEqual(safe_progress_event(value), "event=progress_update")

    def test_progress_retention_is_bounded(self):
        values = []
        for _ in range(MAX_RUN_LOG_ENTRIES + 100):
            append_run_event(values, safe_progress_event(PRIVATE))
        self.assertEqual(len(values), MAX_RUN_LOG_ENTRIES)
        self.assert_private(str(values))

    def test_existing_root_and_nonpropagating_handlers_are_installed_idempotently(self):
        root = logging.getLogger()
        logger = logging.getLogger("uvicorn.access")
        root_stream, access_stream = io.StringIO(), io.StringIO()
        root_handler, access_handler = logging.StreamHandler(root_stream), logging.StreamHandler(access_stream)
        from uvicorn.logging import AccessFormatter
        access_handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s'))
        with patch.object(root, "handlers", [root_handler]), patch.object(root, "level", logging.DEBUG), \
                patch.object(logger, "handlers", [access_handler]), patch.object(logger, "propagate", False), \
                patch.object(logger, "level", logging.INFO):
            install_privacy_logging()
            install_privacy_logging()
            logger.info('%s - "%s %s HTTP/%s" %d', PRIVATE, "GET", "/?key=" + PRIVATE, "1.1", 200)
            root.error(PRIVATE, extra={"prompt": PRIVATE})
            self.assertEqual(sum(isinstance(item, PrivacyFilter) for item in access_handler.filters), 1)
            self.assertIn("event=http_request method=GET status=200", access_stream.getvalue())
            self.assert_private(root_stream.getvalue() + access_stream.getvalue())

    def test_founder_run_callback_and_failure_never_retain_source_text(self):
        # Prevent real config reads and execute only an inert local test callback.
        with patch.dict(os.environ, {}, clear=True), patch("dotenv.load_dotenv"):
            from jobagent.api import runs

        class InlineThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

        def target(progress):
            progress("=== prepare: fetching ===")
            for _ in range(MAX_RUN_LOG_ENTRIES + 5):
                progress(PRIVATE)
            raise ValueError(PRIVATE)

        with patch.object(runs, "RUNS", {}), patch.object(runs.threading, "Thread", InlineThread):
            result = runs.get_run(runs._start(target))
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["log"][-1], "ERROR: ValueError")
            self.assertEqual(len(result["log"]), MAX_RUN_LOG_ENTRIES)
            self.assert_private(str(result))


if __name__ == "__main__":
    unittest.main()
