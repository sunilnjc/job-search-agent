"""The shipped app must mount customer billing without bypassing identity."""
import unittest
from fastapi.testclient import TestClient
from jobagent.mobile.app import create_app
from test_mobile_api import SETTINGS


class BillingRouterIntegrationTests(unittest.TestCase):
    def test_customer_routes_are_mounted_and_require_authentication(self):
        with TestClient(create_app(settings=SETTINGS)) as client:
            for method, path in (("GET", "plans"), ("GET", "account"), ("POST", "reconcile")):
                with self.subTest(path=path):
                    response = client.request(method, "/api/mobile/billing/" + path, **({"json": {}} if method == "POST" else {}))
                    self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
