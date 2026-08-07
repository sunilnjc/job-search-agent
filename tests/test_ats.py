from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from jobagent.applying.ats import (
    ATSDetection,
    choose_apply_link,
    detect_from_html,
    detect_from_url,
    is_aggregator,
)
from jobagent.sources.ats_boards import ATSBoardsSource
from jobagent.sources.validation import check_live_job_link


class DetectFromUrlTests(unittest.TestCase):
    def test_recognises_common_ats_domains(self):
        cases = {
            "https://boards.greenhouse.io/acme/jobs/4123": "greenhouse",
            "https://job-boards.greenhouse.io/acme/jobs/1": "greenhouse",
            "https://jobs.lever.co/acme/9f2a-1": "lever",
            "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/R1": "workday",
            "https://jobs.ashbyhq.com/acme/abc": "ashby",
            "https://jobs.smartrecruiters.com/Acme/74400": "smartrecruiters",
            "https://apply.workable.com/acme/j/ABC123/": "workable",
            "https://careers.icims.com/jobs/1234/login": "icims",
            "https://acme.taleo.net/careersection/jobdetail.ftl": "taleo",
            "https://jobs.personio.de/job/12345": "personio",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(detect_from_url(url), expected)

    def test_returns_none_for_unknown_or_aggregator_urls(self):
        for url in [
            "https://www.adzuna.co.uk/details/5732354357",
            "https://weworkremotely.com/remote-jobs/acme-engineer",
            "https://careers.caterpillar.com/en/jobs/r0000380993/",
        ]:
            with self.subTest(url=url):
                self.assertIsNone(detect_from_url(url))


class DetectFromHtmlTests(unittest.TestCase):
    def test_finds_ats_embedded_in_a_career_page(self):
        # The common case the URL alone misses: employer domain, ATS in an iframe.
        html = '<div><iframe src="https://boards.greenhouse.io/embed/job_app?token=99"></iframe></div>'
        self.assertEqual(detect_from_html(html), "greenhouse")

    def test_finds_client_mounted_ats(self):
        self.assertEqual(detect_from_html('<script src="https://jobs.lever.co/embed.js">'), "lever")

    def test_returns_none_when_no_ats_present(self):
        self.assertIsNone(detect_from_html("<html><body>Email your CV to jobs@acme.com</body></html>"))


class AggregatorTests(unittest.TestCase):
    def test_identifies_aggregators(self):
        for url in [
            "https://www.adzuna.de/details/1",
            "https://weworkremotely.com/remote-jobs/x",
            "https://remoteok.com/remote-jobs/1",
            "https://www.linkedin.com/jobs/view/123",
        ]:
            with self.subTest(url=url):
                self.assertTrue(is_aggregator(url))

    def test_real_application_pages_are_not_aggregators(self):
        for url in ["https://boards.greenhouse.io/acme/jobs/1", "https://careers.acme.com/apply"]:
            with self.subTest(url=url):
                self.assertFalse(is_aggregator(url))


class ATSDetectionTests(unittest.TestCase):
    def test_supported_reflects_whether_an_ats_was_identified(self):
        self.assertTrue(
            ATSDetection(ats="greenhouse", final_url="u", resolved=True, is_aggregator=False).supported
        )
        self.assertFalse(
            ATSDetection(ats=None, final_url="u", resolved=True, is_aggregator=True).supported
        )


class ApplyLinkSelectionTests(unittest.TestCase):
    def test_prefers_external_recognised_ats_apply_link(self):
        selected = choose_apply_link(
            "https://www.adzuna.de/details/42",
            [
                ("/details/42", "Apply now"),
                ("https://jobs.lever.co/acme/123", "Apply for this job"),
                ("https://acme.example/careers", "Company careers"),
            ],
        )
        self.assertEqual(selected, "https://jobs.lever.co/acme/123")

    def test_never_returns_another_aggregator_link(self):
        self.assertIsNone(
            choose_apply_link(
                "https://remoteok.com/remote-jobs/42",
                [("https://www.adzuna.com/details/99", "Apply")],
            )
        )


class DirectBoardSourceTests(unittest.TestCase):
    @patch("jobagent.sources.ats_boards.httpx.get")
    def test_ashby_feed_becomes_direct_employer_postings(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {"jobs": [{
            "id": "a1", "title": "Senior Software Engineer", "location": "Remote — Europe",
            "jobUrl": "https://jobs.ashbyhq.com/acme/a1", "descriptionPlain": "Build systems",
            "address": {"addressCountry": "Germany"},
        }]}
        get.return_value = response
        jobs = ATSBoardsSource()._search_ashby("acme")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source, "ashby:acme")
        self.assertEqual(jobs[0].country, "Germany")


class DirectLinkValidationTests(unittest.TestCase):
    @patch("jobagent.sources.validation.httpx.get")
    def test_expired_page_is_rejected(self, get):
        response = Mock(status_code=200, text="This job is no longer available")
        response.url = "https://jobs.example/expired"
        get.return_value = response
        result = check_live_job_link("https://jobs.example/expired")
        self.assertFalse(result.available)
        self.assertIn("unavailable", result.reason)

    @patch("jobagent.sources.validation.httpx.get")
    def test_live_page_is_accepted(self, get):
        response = Mock(status_code=200, text="Apply for this opportunity")
        response.url = "https://jobs.example/live"
        get.return_value = response
        self.assertTrue(check_live_job_link("https://jobs.example/live").available)

    @patch("jobagent.sources.validation.httpx.get")
    def test_access_denied_requires_manual_check_not_expiry(self, get):
        response = Mock(status_code=403, text="Access denied")
        response.url = "https://jobs.example/protected"
        get.return_value = response
        result = check_live_job_link("https://jobs.example/protected")
        self.assertIsNone(result.available)
        self.assertIn("blocked", result.reason)


if __name__ == "__main__":
    unittest.main()
