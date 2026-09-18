import unittest

from fastapi.testclient import TestClient

from jobagent.mobile.app import create_app
from jobagent.mobile.repository import SupabaseSettings


class MobileReadyzTests(unittest.TestCase):
    def test_readyz_returns_json_when_settings_valid(self):
        client = TestClient(create_app(settings=SupabaseSettings("https://example.supabase.co", "sb_publishable_fixture")))
        for path in ("/readyz", "/api/mobile/readyz"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("content-type", "").split(";")[0], "application/json")
                self.assertEqual(response.headers.get("cache-control"), "no-store")
                self.assertEqual(response.json(), {
                    "status": "ready", "service": "job-pursuit-mobile", "scope": "configuration",
                })
        client.close()

    def test_readyz_not_ready_when_settings_invalid(self):
        client = TestClient(create_app(settings=SupabaseSettings("", "")))
        for path in ("/readyz", "/api/mobile/readyz"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json().get("status"), "not_ready")
        client.close()


if __name__ == "__main__":
    unittest.main()
