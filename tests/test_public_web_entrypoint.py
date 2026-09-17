import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from jobagent.mobile.repository import SupabaseSettings
from jobagent.mobile.web import create_web_app


class PublicWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "assets").mkdir()
        (self.root / "index.html").write_text("<html>Customer workspace</html>")
        (self.root / "assets" / "index-a.js").write_text("/* public build */")
        (self.root / ".env").write_text("MUST_NOT_LEAK=private")
        self.client = TestClient(create_web_app(self.root, settings=SupabaseSettings("https://example.supabase.co", "sb_publishable_fixture")))

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_customer_ui_and_assets(self):
        self.assertEqual(self.client.get("/beta").status_code, 200)
        self.assertEqual(self.client.get("/").url.path, "/beta")
        self.assertEqual(self.client.get("/assets/index-a.js").status_code, 200)
        self.assertEqual(self.client.get("/beta").headers["cache-control"], "no-store, no-store")

    def test_founder_private_files_and_unknown_paths_never_render(self):
        for path in ("/admin", "/api/jobs", "/.env", "/src/jobagent/mobile/web.py", "/beta/anything", "/beta/trust", "/assets/%2e%2e/.env"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("MUST_NOT_LEAK", response.text)

    def test_api_is_still_authenticated(self):
        self.assertEqual(self.client.get("/api/mobile/health").status_code, 200)
        self.assertEqual(self.client.get("/api/mobile/bootstrap").status_code, 401)
        self.assertEqual(self.client.get("/api/mobile/account").status_code, 401)

    def test_ready_requires_build_but_liveness_does_not(self):
        self.assertEqual(self.client.get("/readyz").status_code, 200)
        (self.root / "index.html").unlink()
        self.assertEqual(self.client.get("/readyz").status_code, 503)
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/beta").status_code, 503)

    def test_trust_page_is_off_by_default_and_serves_only_when_flagged(self):
        self.assertEqual(self.client.get("/beta/trust").status_code, 404)
        self.assertEqual(self.client.get("/trust").status_code, 404)
        with patch.dict(os.environ, {"MOBILE_TRUST_PAGE_ENABLED": "true"}):
            flagged = TestClient(create_web_app(self.root, settings=SupabaseSettings("https://example.supabase.co", "sb_publishable_fixture")))
            self.addCleanup(flagged.close)
            self.assertEqual(flagged.get("/beta/trust").status_code, 200)
            self.assertEqual(flagged.get("/beta/trust").headers["cache-control"], "no-store, no-store")
            self.assertEqual(flagged.get("/trust").url.path, "/beta/trust")
            self.assertEqual(flagged.get("/beta/anything").status_code, 404)

    def test_root_public_symlinks_cannot_escape_build(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "private.txt"
            secret.write_text("MUST_NOT_LEAK")
            (self.root / "favicon.svg").symlink_to(secret)
            (self.root / "assets" / "secret.js").symlink_to(secret)
            self.assertEqual(self.client.get("/favicon.svg").status_code, 404)
            self.assertEqual(self.client.get("/assets/secret.js").status_code, 404)


if __name__ == "__main__":
    unittest.main()
