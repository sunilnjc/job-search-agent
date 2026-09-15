"""Durable commercial journey: real React/Chromium -> real ASGI -> real SQL.

Only Stripe HTTP and the auth dependency are synthetic. No payment page is
contacted, no live provider or Supabase request can escape the shared fixture.
Requires existing disposable PostgreSQL binaries and the loopback 5187 fixture
preview from build-workflow-fixture.mjs. Missing SQL runtime is a failing gate,
not a fallback simulation or skip. Evidence lives in node_modules/.cache only.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from test_mobile_billing_customer import CommercialFixture, SQLRepo, A

spec = importlib.util.spec_from_file_location("billing_browser_fixture", Path(__file__).with_name("check-billing-ui.py"))
browser_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser_fixture)
fixture = browser_fixture.fixture
OUT = ROOT / "web/node_modules/.cache/billing-customer-e2e"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    evidence, traffic, screenshots = [], [], []
    try:
        commercial = CommercialFixture()
    except Exception:
        (OUT / "results.json").write_text(json.dumps({"passed":0,"blocked":True,"real_sql_verified":False,
            "reason":"Disposable PostgreSQL initialization failed; see command error. No commercial journey was executed.",
            "live_services":False,"emails":0,"ai_calls":0,"real_charges":0},indent=2)+"\n")
        raise
    try:
        with TestClient(commercial.app) as api, sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            def context_for(width):
                context, page, state = fixture.context_for(browser, width)

                def billing(route):
                    req = route.request
                    assert req.headers.get("authorization", "").startswith("Bearer ")
                    path = urlsplit(req.url).path
                    assert path.startswith("/api/mobile/billing/")
                    traffic.append({"method":req.method,"path":path})
                    # Boundary bridge only: real router/service/SQL execute all
                    # responses. Browser-supplied status is never synthesized.
                    response = api.request(req.method, path, content=req.post_data,
                                           headers={"content-type":"application/json"})
                    route.fulfill(status=response.status_code, body=response.content, content_type="application/json")

                def bootstrap(route):
                    state_now = api.get("/api/mobile/billing/account").json()
                    if not state_now["access"]["allowed"]:
                        route.fulfill(status=403, json={"detail":"Your account needs workspace access. Billing remains available."})
                    else:
                        route.fallback()  # Non-billing workspace is the existing fixture.

                context.route(re.compile(r"^http://127\.0\.0\.1:5187/api/mobile/billing(?:/.*)?$"), billing)
                context.route("**/api/mobile/bootstrap", bootstrap)
                return context, page, state

            def capture(page, label):
                fixture.no_overflow(page)
                name = label + ".png"
                page.screenshot(path=str(OUT / name), full_page=True)
                screenshots.append(name)

            def check(label):
                evidence.append(label)
                print("PASS " + label, flush=True)

            context, page, state = context_for(390)
            browser_fixture.open_billing(page, unpaid=True)
            expect(page.get_by_test_id("billing-access")).to_contain_text("not enabled")
            expect(page.get_by_test_id("billing-plan-summary")).to_contain_text("3.21")
            expect(page.get_by_test_id("billing-plan-summary")).to_contain_text("100 AI units")
            assert not any(r["method"] == "POST" for r in traffic)
            capture(page,"01-no-membership-price")
            check("No membership: pricing visible, forged success query ignored, zero implicit billing writes")

            page.get_by_role("button",name="Create test checkout link",exact=True).click()
            expect(page.get_by_role("link",name="Open Stripe test checkout",exact=False)).to_have_attribute("href","https://checkout.stripe.com/c/pay/fixture")
            expect(page.get_by_test_id("billing-access")).to_contain_text("not enabled")
            capture(page,"02-checkout-not-payment")
            expect(page.get_by_role("button",name="Verify payment & access",exact=True)).to_be_enabled()
            check("Explicit sandbox checkout creates durable intent but grants no access")

            commercial.settle()  # Synthetic provider-side payment; NOT a charge.
            page.get_by_role("button",name="Refresh billing",exact=True).click()
            expect(page.get_by_role("button",name="Verify payment & access",exact=True)).to_be_enabled()
            expect(page.get_by_test_id("billing-access")).to_contain_text("not enabled")
            page.get_by_role("button",name="Verify payment & access",exact=True).click()
            expect(page.get_by_test_id("billing-access")).to_have_text("Workspace access is enabled.")
            expect(page.get_by_role("button",name="Create test checkout link",exact=True)).to_be_disabled()
            capture(page,"03-verified-access")
            check("Real reconciliation SQL grants access only after canonical subscription/invoice/charge proof")

            page.get_by_role("button",name="Create test portal link",exact=True).click()
            expect(page.get_by_role("link",name="Open Stripe test portal",exact=False)).to_be_visible()
            current = api.get("/api/mobile/billing/account").json()
            assert current["subscription"]["cancel_at_period_end"] is False
            commercial.stripe.sub["cancel_at_period_end"] = True
            commercial.expire_cooldown()
            page.get_by_role("button",name="Verify payment & access",exact=True).click()
            expect(page.locator(".billing-details").last).to_contain_text("Scheduled")
            expect(page.get_by_test_id("billing-access")).to_have_text("Workspace access is enabled.")
            capture(page,"04-cancel-at-period-end")
            check("Portal opening alone does not cancel; verified scheduled cancellation preserves paid-period access")
            browser_fixture.clean(state)
            context.close()

            # Fresh browser storage and repository, same durable billing database.
            # Auth session establishment is a fixture, not proof of hosted login.
            commercial.repo = SQLRepo(commercial.db,A)
            context, page, state = context_for(1440)
            writes_before = len([r for r in traffic if r["method"] == "POST"])
            browser_fixture.open_billing(page)
            expect(page.get_by_test_id("billing-access")).to_have_text("Workspace access is enabled.")
            expect(page.locator(".billing-details").last).to_contain_text("Scheduled")
            assert len([r for r in traffic if r["method"] == "POST"]) == writes_before
            capture(page,"05-fresh-session")
            check("Fresh signed-in browser reads persisted entitlement/cancellation; no local paid flag or automatic reconciliation")
            commercial.stripe.sub["status"] = "canceled"
            commercial.expire_cooldown()
            page.get_by_role("button",name="Verify payment & access",exact=True).click()
            expect(page.get_by_test_id("billing-access")).to_contain_text("not enabled")
            capture(page,"06-cancelled-no-access")
            check("Canonical terminal cancellation disables billing-owned access")
            browser_fixture.clean(state)
            context.close()
            browser.close()
        assert all(r.method != "DELETE" for r in commercial.stripe.requests)
        (OUT / "results.json").write_text(json.dumps({"passed":len(evidence),"scenarios":evidence,
            "screenshots":screenshots,"browser_requests":traffic,"real_react":True,"real_asgi":True,"real_sql":True,
            "synthetic_auth":True,"synthetic_stripe":True,"provider_http_count":len(commercial.stripe.requests),
            "live_services":False,"emails":0,"ai_calls":0,"real_charges":0},indent=2)+"\n")
    finally:
        commercial.close()


if __name__ == "__main__":
    main()
