"""JP029: fresh config + isolated submit spies; never run a real worker or provider."""
from __future__ import annotations

import os
import socket
import subprocess
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

# Suppress the owner's .env before product imports; never inspect its mode/key.
with patch.dict(os.environ, {}, clear=True), patch("dotenv.load_dotenv"):
    from jobagent.applying import autopilot, greenhouse
    from jobagent.applying.ats import ATSDetection
    from jobagent.applying.greenhouse import SubmissionResult
    from jobagent.config import Settings
    from jobagent.tracking import pipeline


class AutopilotSubmissionModeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(patch("dotenv.load_dotenv"))
        for name in ("connect", "connect_ex", "sendto"):
            self.stack.enter_context(patch.object(socket.socket, name, side_effect=AssertionError("No network")))
        self.stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=AssertionError("No DNS")))
        self.stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("No browser/process launch")))
        self.stack.enter_context(patch.object(autopilot.db, "connection", side_effect=AssertionError("No real database")))
        self.stack.enter_context(patch.object(autopilot.db, "init_db", side_effect=AssertionError("No worker/database initialization")))
        self.create = self.stack.enter_context(patch.object(autopilot.db, "create_application_attempt", return_value=17))
        self.update = self.stack.enter_context(patch.object(autopilot.db, "update_application_attempt"))
        self.transition = self.stack.enter_context(patch.object(pipeline, "transition"))
        self.stack.enter_context(patch.object(autopilot, "parse_resume", side_effect=AssertionError("No real resume/provider")))
        self.stack.enter_context(patch.object(autopilot, "draft_job", side_effect=AssertionError("No real drafting/provider")))
        self.cover, self.resume = Mock(spec=Path), Mock(spec=Path)
        self.cover.is_file.return_value = self.resume.is_file.return_value = True
        self.stack.enter_context(patch.object(autopilot, "document_paths", return_value=(self.cover, self.resume)))
        self.fallback_submit = self.stack.enter_context(patch.object(
            greenhouse, "submit", return_value=SubmissionResult("submitted")))
        self.submit = Mock(return_value=SubmissionResult("submitted"))
        self.resolver = Mock(return_value=ATSDetection("greenhouse", "https://synthetic.example.test/job/1", False, False))
        self.preflight = Mock(return_value=SubmissionResult("ready_for_submission"))
        self.job = {"id": 1, "title": "Synthetic Role", "company": "Synthetic Employer",
                    "url": "https://synthetic.example.test/job/1"}
        self.conn = object()

    def settings_for(self, value=None):
        environment = {} if value is None else {"AUTOPILOT_AUTO_SUBMIT": value}
        with patch.dict(os.environ, environment, clear=True):
            return Settings()

    def prepare(self, settings, *, use_injected_submit=True):
        # Only exercise the single-job decision boundary under complete doubles.
        # No queue/direct-apply entrypoint or scheduled worker is invoked.
        with patch.object(autopilot, "settings", settings):
            return autopilot.process_job(
                self.conn, self.job, resolver=self.resolver, profile=object(),
                greenhouse_preflight=self.preflight,
                greenhouse_submit=self.submit if use_injected_submit else None,
            )

    def assert_preparation_only(self, result):
        self.assertEqual(result.state, "ready_for_submission")
        self.assertEqual(result.attempt_id, 17)
        self.submit.assert_not_called()
        self.fallback_submit.assert_not_called()
        self.transition.assert_not_called()
        self.assertFalse(any(call.kwargs.get("submitted") for call in self.update.call_args_list))
        self.assertFalse(any(call.kwargs.get("state") == "submitted" for call in self.update.call_args_list))
        self.assertEqual(self.update.call_args.kwargs["state"], "ready_for_submission")

    def test_absent_setting_defaults_false_and_never_invokes_injected_submit(self):
        config = self.settings_for()
        self.assertIs(config.autopilot_auto_submit, False)
        self.assert_preparation_only(self.prepare(config))
        self.preflight.assert_called_once()

    def test_default_does_not_fall_through_to_production_submit_import(self):
        self.assert_preparation_only(self.prepare(self.settings_for(), use_injected_submit=False))

    def test_false_empty_and_unrecognized_values_never_opt_in(self):
        for value in ("false", "FALSE", "", "0", "1", "yes", "on", "auto", " true "):
            with self.subTest(value=value):
                config = self.settings_for(value)
                self.assertIs(config.autopilot_auto_submit, False)
                self.assert_preparation_only(self.prepare(config))

    def test_existing_explicit_true_override_preserved_and_reaches_submit_spy(self):
        config = self.settings_for("true")
        self.assertIs(config.autopilot_auto_submit, True)
        result = self.prepare(config)
        self.assertEqual(result.state, "submitted")
        self.submit.assert_called_once_with(
            {"final_url": "https://synthetic.example.test/job/1", "title": self.job["title"], "company": self.job["company"]},
            self.resume, self.cover)
        self.fallback_submit.assert_not_called()
        self.transition.assert_called_once_with(self.conn, 1, "applied")
        self.assertTrue(self.update.call_args.kwargs["submitted"])

    def test_case_insensitive_explicit_true_reaches_mocked_default_handler(self):
        config = self.settings_for("TrUe")
        self.assertIs(config.autopilot_auto_submit, True)
        self.assertEqual(self.prepare(config, use_injected_submit=False).state, "submitted")
        self.fallback_submit.assert_called_once()
        self.submit.assert_not_called()

    def test_explicit_opt_in_does_not_override_preflight_exception(self):
        self.preflight.return_value = SubmissionResult("exception", "captcha_required")
        result = self.prepare(self.settings_for("true"))
        self.assertEqual((result.state, result.reason), ("exception", "captcha_required"))
        self.submit.assert_not_called()
        self.fallback_submit.assert_not_called()
        self.transition.assert_not_called()

    def test_explicit_opt_in_does_not_mark_unconfirmed_submission_applied(self):
        self.submit.return_value = SubmissionResult("exception", "confirmation_not_observed")
        result = self.prepare(self.settings_for("true"))
        self.assertEqual(result.state, "exception")
        self.submit.assert_called_once()
        self.transition.assert_not_called()
        self.assertIs(self.update.call_args.kwargs["submitted"], False)

    def test_opt_in_does_not_route_other_ats_to_greenhouse_submit(self):
        self.resolver.return_value = ATSDetection("lever", "https://synthetic.example.test/job/1", False, False)
        self.assert_preparation_only(self.prepare(self.settings_for("true")))


if __name__ == "__main__":
    unittest.main()
