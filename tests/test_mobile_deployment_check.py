"""Offline edge probes: never send tokens or consume founder response bodies."""
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx

SPEC = importlib.util.spec_from_file_location("mobile_deployment_check", Path(__file__).resolve().parents[1] / "scripts/mobile_deployment_check.py")
edge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = edge
SPEC.loader.exec_module(edge)


class UnreadBody(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError("Private/redirect page body must not be read")


def response(request):
    if request.url.path in edge.PROTECTED_PATHS:
        return httpx.Response(302, headers={"Location": "https://synthetic.cloudflareaccess.com/cdn-cgi/access/login/example.test?meta=do-not-follow"}, stream=UnreadBody())
    if request.url.path == "/beta":
        return httpx.Response(200, headers={"Content-Type": "text/html"}, stream=UnreadBody())
    if request.url.path == "/api/mobile/bootstrap":
        return httpx.Response(401, stream=UnreadBody())
    return httpx.Response(200, json={"status": "ok", "service": "job-pursuit-mobile"})


class DeploymentCheckTests(unittest.TestCase):
    def test_exact_routes_and_no_credentials_or_redirect_following(self):
        calls = []
        def dispatch(req):
            calls.append(req)
            return response(req)
        results = edge.run("https://example.test", transport=httpx.MockTransport(dispatch))
        self.assertTrue(all(item.passed for item in results))
        self.assertEqual([req.url.path for req in calls], list(edge.PATHS))
        self.assertTrue(all(req.method == "GET" and req.url.host == "example.test" for req in calls))
        self.assertTrue(all("authorization" not in req.headers and "apikey" not in req.headers for req in calls))

    def test_exposed_founder_json_is_failure_without_reading_body(self):
        results = edge.run("https://example.test", transport=httpx.MockTransport(lambda req: httpx.Response(200, headers={"Content-Type": "application/json"}, stream=UnreadBody()) if req.url.path in edge.PROTECTED_PATHS else response(req)))
        self.assertTrue(all(not item.passed for item in results if item.path in edge.PROTECTED_PATHS))

    def test_mobile_prefix_lookalikes_must_remain_protected(self):
        for path in ("/api/mobile-extra", "/api/mobileevil", "/api"):
            with self.subTest(path=path):
                results = edge.run("https://example.test", transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=UnreadBody()) if req.url.path == path else response(req)))
                self.assertFalse(next(item.passed for item in results if item.path == path))

    def test_arbitrary_or_spoofed_redirect_is_not_protection(self):
        for location in ("https://unrelated.example/login", "https://badcloudflareaccess.com/cdn-cgi/access/login/example.test", "https://team.cloudflareaccess.com.evil.test/cdn-cgi/access/login/x", "http://team.cloudflareaccess.com/cdn-cgi/access/login/x", "https://team.cloudflareaccess.com/other", "https://user:pass@team.cloudflareaccess.com/cdn-cgi/access/login/x"):
            with self.subTest(location=location):
                self.assertFalse(edge.protected(httpx.Response(302, headers={"Location": location})))
        for status in (401, 403):
            self.assertTrue(edge.protected(httpx.Response(status)))
        self.assertFalse(edge.protected(httpx.Response(404)))

    def test_health_requires_exact_bounded_public_payload(self):
        for payload in (b"not json", b"x" * 2048, b'{"status":"ok"}', json.dumps({"status": "ok", "service": "founder"}).encode()):
            with self.subTest(size=len(payload)):
                results = edge.run("https://example.test", transport=httpx.MockTransport(lambda req: httpx.Response(200, content=payload) if req.url.path == "/api/mobile/health" else response(req)))
                self.assertFalse(results[-2].passed)

    def test_offline_default_never_constructs_client(self):
        with patch.object(edge.httpx, "Client", side_effect=AssertionError("offline")), redirect_stdout(io.StringIO()):
            self.assertEqual(edge.main([]), 0)

    def test_invalid_origin_rejected_before_network(self):
        for origin in ("http://example.test", "https://user:secret@example.test", "https://example.test/path", "https://example.test?secret=hidden", "https://example.test/#fragment", "https://example.test\\@bad.test"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                edge.validate_origin(origin)

    def test_network_failures_are_safe_status_only(self):
        def fail(req):
            raise httpx.ConnectError("private diagnostic withheld")
        results = edge.run("https://example.test", transport=httpx.MockTransport(fail))
        self.assertTrue(all(not item.passed and item.status == 0 for item in results))
        self.assertNotIn("diagnostic", repr(results))


if __name__ == "__main__":
    unittest.main()
