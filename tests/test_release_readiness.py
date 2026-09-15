"""Offline release probe regression: stale servers cannot pass as repaired."""
from pathlib import Path
import sys
import unittest

import httpx
from fastapi.testclient import TestClient
from jobagent.mobile.app import create_app
from jobagent.mobile.release import CORE_FLOW_CONTRACT, public_release

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from release_readiness_check import run


class ReleaseReadinessTests(unittest.TestCase):
    def test_current_pair_passes_without_credentials(self):
        requests = []
        def handler(request):
            requests.append(request)
            self.assertNotIn("authorization", request.headers)
            self.assertNotIn("cookie", request.headers)
            self.assertEqual(request.method, "GET")
            if request.url.path.endswith("version"):
                return httpx.Response(200, json=public_release(), headers={"Set-Cookie": "untrusted=value"})
            return httpx.Response(200, text=f'<meta name="job-pursuit-workflow" content="{CORE_FLOW_CONTRACT}">', headers={"Content-Type": "text/html"})
        self.assertTrue(all(x["contract_matches"] for x in run("https://example.test", transport=httpx.MockTransport(handler))))
        self.assertEqual(len(requests), 2)

    def test_stale_and_redirect_are_not_releases(self):
        for status in (200, 302, 404, 503):
            def handler(request):
                return httpx.Response(status, text="old build", headers={"Content-Type": "text/html", "Location": "https://evil.test"})
            self.assertFalse(any(x["contract_matches"] for x in run("https://example.test", transport=httpx.MockTransport(handler))))

    def test_marker_is_synchronized_and_duplicate_rejected(self):
        self.assertIn(CORE_FLOW_CONTRACT, (ROOT / "web/index.html").read_text())
        def handler(request):
            marker = f'<meta name="job-pursuit-workflow" content="{CORE_FLOW_CONTRACT}">'
            return httpx.Response(200, text=marker * 2, headers={"Content-Type": "text/html"})
        self.assertFalse(run("https://example.test", transport=httpx.MockTransport(handler))[1]["contract_matches"])

    def test_public_identity_is_minimal_and_health_unchanged(self):
        with TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/mobile/version").json(), public_release())
            self.assertEqual(client.get("/api/mobile/health").json(), {"status": "ok", "service": "job-pursuit-mobile"})
            self.assertEqual(client.get("/api/mobile/version").headers["cache-control"], "no-store")
