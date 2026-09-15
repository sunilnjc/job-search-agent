"""Real React + local Chromium, intercepted billing API only; never contacts Stripe.

The shared repository harness blocks all external traffic. These assertions prove
client behaviour, not provider payments, webhook reconciliation or hosted access.
"""
import copy
import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

spec = importlib.util.spec_from_file_location("workflow_fixture", Path(__file__).with_name("check-workflow-ui.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
expect.set_options(timeout=20000)
READY = {"provider": "stripe", "mode": "test", "checkout_enabled": True, "portal_enabled": True, "configuration_ready": True, "plan_keys": ["operator_test_fixture"], "subscription": None, "subscription_status": "none"}
READY.update(account_exists=True, reconciliation_pending=False, access={"allowed":False, "grant_source":None,
    "expires_at":None,"period_end":None,"period_remaining":0,"daily_remaining":0})
PLANS = {"mode":"test","plans":[{"plan_key":"operator_test_fixture","display_name":"Sandbox Career",
    "amount_minor":321,"currency":"usd","interval":"month","interval_count":1,"period_limit":100,"daily_limit":10}]}
URLS = {"checkout": "https://checkout.stripe.com/c/pay/cs_test_offline_fixture#original", "portal": "https://billing.stripe.com/p/session/test_offline_fixture"}


def billing_context(browser, width=390, scheme="light", mode="ready", unpaid=False):
    context, page, base = fixture.context_for(browser, width, scheme)
    state = {"gets": 0, "writes": [], "mode": mode, "status": copy.deepcopy(READY)}
    if unpaid:
        context.route("**/api/mobile/bootstrap", lambda route: route.fulfill(status=403, json={"detail": "Synthetic membership required; invited billing remains available."}))
    if mode == "disabled":
        state["status"].update(provider=None, checkout_enabled=False, portal_enabled=False, configuration_ready=False, plan_keys=[], subscription_status="not_loaded")
    if mode == "subscription":
        state["status"].update(subscription_status="active", subscription={"status": "active", "plan_key": "operator_test_fixture", "period_start": "2026-09-01T00:00:00Z", "paid_through": "2026-10-01T00:00:00Z", "cancel_at_period_end": True, "cancellation_status": "scheduled"})
    if mode in ("live_unapproved", "live_approved"):
        state["status"].update(mode="live",live_activation_approved=mode=="live_approved")

    def route_billing(route):
        request = route.request
        path = urlsplit(request.url).path
        assert request.headers.get("authorization", "").startswith("Bearer ") and "apikey" not in request.headers
        if request.method == "GET" and path == "/api/mobile/billing/account":
            state["gets"] += 1
            if state["mode"] == "invite_denied":
                route.fulfill(status=403, json={"detail": "This account is not invited to test billing. Contact the service operator."})
            else:
                route.fulfill(json=state["status"])
        elif request.method == "GET" and path == "/api/mobile/billing/plans":
            route.fulfill(json={**PLANS,"mode":state["status"]["mode"]})
        elif request.method == "POST" and path in ("/api/mobile/billing/checkout", "/api/mobile/billing/portal"):
            body = request.post_data_json
            kind = path.rsplit("/", 1)[1]
            assert body == ({"plan_key": "operator_test_fixture"} if kind == "checkout" else {})
            state["writes"].append((path, body))
            if state["mode"] == "write_failure":
                route.fulfill(status=503, json={"detail": {"code": "billing_reconcile_required", "message": "Synthetic billing operation pending. Contact the operator before retrying."}})
            else:
                route.fulfill(json={"url": "https://checkout.stripe.com.attacker.invalid/" if state["mode"] == "unsafe" else URLS[kind]})
        else:
            raise AssertionError((path, request.method))
    context.route(re.compile(r"^http://127\.0\.0\.1:5187/api/mobile/billing(?:/.*)?$"), route_billing)
    return context, page, state, base


def open_billing(page, unpaid=False):
    # A client return URL cannot cause POSTs, grants or optimistic paid status.
    page.goto(fixture.BASE + "?checkout=success&paid=true")
    if not unpaid:
        page.get_by_role("button", name="Your profile", exact=True).click()
    page.get_by_role("button", name=re.compile(r"^(Billing test mode|Plans & access)$")).click()
    expect(page.get_by_role("heading", name="Choose your job-search plan", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Refresh billing", exact=True)).to_be_enabled()


def clean(base):
    assert not base["unexpected"], base["unexpected"]
    assert not base["writes"] and base["rank_calls"] == 0 and base["prepare_calls"] == 0


def main():
    output = Path(__file__).resolve().parents[1] / "node_modules/.cache/billing-ui"
    output.mkdir(parents=True, exist_ok=True)
    evidence = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width, scheme in [(1440, "light"), (390, "light"), (320, "dark")]:
            context, page, state, base = billing_context(browser, width, scheme, unpaid=True)
            open_billing(page, unpaid=True)
            assert state["gets"] == 1 and not state["writes"]
            expect(page.get_by_text("The service returned no subscription record.", exact=False)).to_be_visible()
            page.get_by_role("button", name="Create test checkout link", exact=True).click()
            expect(page.get_by_role("link", name="Open Stripe test checkout", exact=False)).to_have_attribute("href", URLS["checkout"])
            assert state["gets"] == 1 and len(state["writes"]) == 1 and urlsplit(page.url).hostname == "127.0.0.1"
            expect(page.get_by_role("button", name="Create test checkout link", exact=True)).to_be_disabled()
            fixture.no_overflow(page)
            page.screenshot(path=str(output / f"billing-{width}-{scheme}-checkout-link.png"), full_page=True)
            page.get_by_role("button", name="Refresh billing", exact=True).click()
            page.get_by_role("button", name="Create test portal link", exact=True).click()
            expect(page.get_by_role("link", name="Open Stripe test portal", exact=False)).to_have_attribute("href", URLS["portal"])
            assert state["gets"] == 2 and len(state["writes"]) == 2 and state["status"]["subscription"] is None
            page.screenshot(path=str(output / f"billing-{width}-{scheme}-portal-link.png"), full_page=True)
            clean(base)
            evidence.append(f"PASS billing {width}px {scheme}: invited unpaid fallback, explicit checkout/portal URLs only, ignored return query, no Stripe navigation")
            print(evidence[-1])
            context.close()

        for width in (1440, 390):
            # Stripe success/cancel return: opens billing from the query hint only,
            # strips it, loads server status once and never shows paid access.
            for query in ("return", "cancelled"):
                context, page, state, base = billing_context(browser, width)
                page.goto(fixture.BASE + f"?billing={query}&paid=true")
                expect(page.get_by_role("heading", name="Choose your job-search plan", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="Refresh billing", exact=True)).to_be_enabled()
                assert "billing=" not in page.url, page.url
                assert state["gets"] == 1 and not state["writes"] and not state["status"]["access"]["allowed"]
                page.reload()
                expect(page.get_by_role("button", name="Your profile", exact=True)).to_be_visible()
                assert state["gets"] == 1, "refresh after return must not reopen billing"
                fixture.no_overflow(page)
                clean(base)
                evidence.append(f"PASS billing {width}px return={query}: opens billing, strips hint, one status read, no writes or access")
                print(evidence[-1])
                context.close()

        for width, mode in [(1440, "disabled"), (390, "disabled"), (390, "subscription"), (390, "unsafe"), (1440, "write_failure"), (390, "invite_denied"), (390,"live_unapproved"), (1440,"live_approved")]:
            context, page, state, base = billing_context(browser, width, mode=mode)
            open_billing(page)
            if mode == "live_approved":
                expect(page.get_by_role("button",name="Continue to secure checkout",exact=True)).to_be_enabled()
                expect(page.get_by_role("button",name="Manage subscription",exact=True)).to_be_enabled()
                expect(page.get_by_role("heading",name="Available plans",exact=True)).to_be_visible()
                expect(page.get_by_text("Use test payment details only",exact=False)).to_have_count(0)
                assert not state["writes"]  # display only, even in an approved live-mode fixture
            elif mode == "live_unapproved":
                expect(page.get_by_role("alert")).to_contain_text("could not be verified")
                expect(page.get_by_role("button",name="Create test checkout link",exact=True)).to_be_disabled()
                assert not state["writes"]
            elif mode in ("disabled", "invite_denied"):
                expect(page.get_by_role("button", name="Create test checkout link", exact=True)).to_be_disabled()
                expect(page.get_by_role("button", name="Create test portal link", exact=True)).to_be_disabled()
                if mode == "disabled":
                    expect(page.get_by_text("Test billing is not configured.", exact=False)).to_be_visible()
                    expect(page.get_by_text("Subscription status has not loaded.", exact=False)).to_be_visible()
                else:
                    expect(page.get_by_role("alert")).to_contain_text("not invited")
                assert not state["writes"]
            elif mode == "subscription":
                expect(page.get_by_role("button", name="Create test checkout link", exact=True)).to_be_disabled()
                expect(page.get_by_role("button", name="Create test portal link", exact=True)).to_be_enabled()
                expect(page.locator(".billing-details").last).to_contain_text("active")
                expect(page.locator(".billing-details").last).to_contain_text("Scheduled")
                assert not state["writes"]
            else:
                page.get_by_role("button", name="Create test checkout link", exact=True).click()
                expect(page.get_by_role("alert")).to_contain_text("untrusted billing link" if mode == "unsafe" else "Synthetic billing operation pending")
                expect(page.get_by_role("button", name="Create test checkout link", exact=True)).to_be_disabled()
                expect(page.get_by_role("link", name="Open Stripe", exact=False)).to_have_count(0)
                assert len(state["writes"]) == 1 and state["gets"] == 1
            fixture.no_overflow(page)
            page.screenshot(path=str(output / f"billing-{width}-{mode}.png"), full_page=True)
            clean(base)
            evidence.append(f"PASS billing {width}px {mode}: truthful service state, no implicit writes/retry/external navigation")
            print(evidence[-1])
            context.close()
        browser.close()
    (output / "results.json").write_text(json.dumps({"fixture_only": True, "passed": len(evidence), "scenarios": evidence, "live_services": False}, indent=2) + "\n")
    print(f"{len(evidence)} isolated billing browser scenarios passed; no Stripe navigation, payments or access grants.")


if __name__ == "__main__":
    main()
