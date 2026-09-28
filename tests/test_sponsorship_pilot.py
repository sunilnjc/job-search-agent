"""Posting wording must not become a personal immigration determination."""
import unittest

from jobagent.mobile.discovery import _sponsorship_signal


class SponsorshipPilotTests(unittest.TestCase):
    def test_authorization_alone_is_not_sponsorship_refusal(self):
        self.assertEqual(_sponsorship_signal({"description": "Must already be authorized to work in the US."}), "unknown")

    def test_explicit_refusal_stays_restricted(self):
        self.assertEqual(_sponsorship_signal({"description": "We cannot provide visa sponsorship."}), "restricted")

    def test_authorization_qualifies_an_apparent_offer(self):
        self.assertEqual(_sponsorship_signal({"description": "Visa sponsorship is available. Must already be authorized to work in US."}), "conditional")

    def test_transfer_mention_is_not_general_sponsorship_promise(self):
        self.assertNotEqual(_sponsorship_signal({"description": "H-1B transfers considered on a case-by-case basis."}), "offered")

    def test_historical_statement_is_not_current_support(self):
        self.assertEqual(_sponsorship_signal({"description": "We sponsored H-1B workers in 2023."}), "unknown")

    def test_conflicting_posting_does_not_pass_as_offer(self):
        self.assertEqual(_sponsorship_signal({"description": "Visa sponsorship is available. We cannot provide visa sponsorship."}), "conflicting")
