import unittest

from fastapi.testclient import TestClient

from jobagent.mobile.app import create_app
from jobagent.mobile.repository import SupabaseSettings


class MobileReadyzTests(unittest.TestCase):
    def test_readyz_returns_json_when_settings_valid(self):
        client = TestClient(create_app(settings=SupabaseSettings("https://example.supabase.co", "sb_publishable_fixture")))
        response = client.get("/readyz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-type", "").split(";")[0], "application/json")
        body = response.json()
        self.assertEqual(body.get("status"), "ready")
        self.assertEqual(body.get("service"), "job-pursuit-mobile")
        self.assertEqual(body.get("scope"), "configuration")
        client.close()

    def test_readyz_not_ready_when_settings_invalid(self):
        client = TestClient(create_app(settings=SupabaseSettings("", "")))
        response = client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json().get("status"), "not_ready")
        client.close()


if __name__ == "__main__":
    unittest.main()
