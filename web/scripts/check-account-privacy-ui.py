"""Bounded privacy UI checks against the actual React fixture build, not a mock page.

No real auth, email, provider, account deletion or external requests. The shared
fixture intercepts all traffic; only static preview assets reach loopback :5187.
"""
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
EXPORT_ID = "00000000-0000-4000-8000-000000000010"
ERASE_ID = "00000000-0000-4000-8000-000000000011"
STAMP = "2026-09-15T00:00:00Z"


def privacy_context(browser, *, width=390, scheme="light", unpaid=False, processing=True, signed_in=True, verified=True, lost_ack=False, fail_status=False):
    context, page, base = fixture.context_for(browser, width, scheme, signed_in=signed_in, verified_email=verified)
    state = {"requests": [], "gets": 0, "writes": [], "downloads": 0, "lost_ack": lost_ack, "fail_status": fail_status}
    if unpaid:
        context.route("**/api/mobile/bootstrap", lambda route: route.fulfill(status=403, json={"detail": "Synthetic membership required for workspace; privacy remains available."}))

    def route_account(route):
        req = route.request
        path = urlsplit(req.url).path
        assert req.headers.get("authorization", "").startswith("Bearer ")
        assert "apikey" not in req.headers
        if req.method == "GET" and path == "/api/mobile/account":
            state["gets"] += 1
            if state["fail_status"]:
                route.fulfill(status=401, json={"detail": "Synthetic session expired"})
            else:
                route.fulfill(json={"requests": state["requests"], "capability": {"export": True, "erasure": True, "processing_configured": processing}})
        elif req.method == "POST" and path in ("/api/mobile/account/exports", "/api/mobile/account/erasure"):
            body = req.post_data_json
            state["writes"].append((path, body))
            assert "user_id" not in body and "userId" not in body
            kind = "export" if path.endswith("exports") else "erase"
            assert body == ({} if kind == "export" else {"confirmation": "DELETE MY ACCOUNT", "email": "alex@example.test"})
            item = {"id": EXPORT_ID if kind == "export" else ERASE_ID, "kind": kind, "state": "queued" if processing else "blocked", "created_at": STAMP, "completed_at": None, "download_ready": False}
            if not processing:
                item.update(error_code="processor_unconfigured", message="Operator setup required; no processing has occurred.")
            state["requests"].append(item)
            if state["lost_ack"]:
                state["lost_ack"] = False
                route.abort("failed")
            else:
                route.fulfill(status=202, json=item)
        elif req.method == "GET" and path == f"/api/mobile/account/exports/{EXPORT_ID}/download":
            state["downloads"] += 1
            item = next(request for request in state["requests"] if request["id"] == EXPORT_ID)
            assert item["state"] == "complete" and item["download_ready"] is True
            route.fulfill(body=b"synthetic archive bytes", content_type="application/zip", headers={"Content-Disposition": 'attachment; filename="pursuit-account-export.zip"'})
        else:
            raise AssertionError((req.method, path))

    context.route(re.compile(r"^http://127\.0\.0\.1:5187/api/mobile/account(?:/.*)?$"), route_account)
    return context, page, state, base


def open_account(page, *, unpaid=False):
    page.goto(fixture.BASE)
    if not unpaid:
        page.get_by_role("button", name="Your profile", exact=True).click()
    page.get_by_role("button", name="Account privacy", exact=True).click()
    expect(page.get_by_role("heading", name="Account privacy", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Refresh requests", exact=True)).to_be_enabled()


def assert_clean(base):
    assert not base["unexpected"], base["unexpected"]
    assert not base["writes"], "Privacy UI must not mutate workspace resources"


def main():
    output = Path(__file__).resolve().parents[1] / "node_modules/.cache/account-privacy-ui"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width, scheme in [(320, "light"), (390, "dark"), (1440, "light")]:
            context, page, state, base = privacy_context(browser, width=width, scheme=scheme, unpaid=True)
            open_account(page, unpaid=True)
            assert state["gets"] == 1 and not state["writes"]
            page.get_by_role("button", name="Request data export", exact=True).click()
            expect(page.get_by_text("An export request is already pending.", exact=False)).to_be_visible()
            assert state["gets"] == 1 and len(state["writes"]) == 1, "POST acceptance must not auto-poll"
            expect(page.get_by_role("button", name="Download account export", exact=True)).to_have_count(0)
            state["requests"][0].update(state="complete", download_ready=True, completed_at=STAMP)
            page.get_by_role("button", name="Refresh requests", exact=True).click()
            expect(page.get_by_role("button", name="Download account export", exact=True)).to_be_visible()
            with page.expect_download() as downloaded:
                page.get_by_role("button", name="Download account export", exact=True).click()
            assert downloaded.value.suggested_filename == "pursuit-account-export.zip"
            assert state["gets"] == 2 and state["downloads"] == 1 and len(state["writes"]) == 1
            fixture.no_overflow(page)
            page.screenshot(path=str(output / f"privacy-{width}-{scheme}.png"), full_page=True)
            assert_clean(base)
            context.close()
            print(f"PASS unpaid privacy/export/manual refresh/download {width}px {scheme}")

        context, page, state, base = privacy_context(browser)
        open_account(page)
        page.get_by_role("button", name="Review account erasure", exact=True).click()
        assert not state["writes"], "First erasure step must be read-only"
        expect(page.get_by_role("button", name="Queue erasure request", exact=True)).to_be_disabled()
        page.get_by_role("textbox", name="Type DELETE MY ACCOUNT", exact=True).fill("DELETE MY ACCOUNT ")
        page.get_by_role("checkbox", name="I understand this queues", exact=False).check()
        expect(page.get_by_role("button", name="Queue erasure request", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Go back without requesting erasure", exact=True).click()
        assert not state["writes"]
        page.get_by_role("button", name="Review account erasure", exact=True).click()
        page.get_by_role("textbox", name="Type DELETE MY ACCOUNT", exact=True).fill("DELETE MY ACCOUNT")
        page.get_by_role("checkbox", name="I understand this queues", exact=False).check()
        page.get_by_role("button", name="Queue erasure request", exact=True).click()
        expect(page.get_by_text("This is not confirmation that your account or data has been erased.", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Review account erasure", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Request data export", exact=True)).to_be_disabled()
        expect(page.get_by_text("An erasure request is already pending and workspace access is frozen.", exact=False)).to_be_visible()
        assert len(state["writes"]) == 1 and state["writes"][0][1]["email"] == "alex@example.test" and state["gets"] == 1
        fixture.no_overflow(page)
        page.screenshot(path=str(output / "privacy-390-erasure-queued.png"), full_page=True)
        assert_clean(base)
        context.close()
        print("PASS profile entry and explicit two-step queued erasure without immediate deletion claim")

        context, page, state, base = privacy_context(browser, unpaid=True, lost_ack=True)
        open_account(page, unpaid=True)
        page.get_by_role("button", name="Request data export", exact=True).click()
        expect(page.get_by_text("The last request may have been accepted.", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Request data export", exact=True)).to_be_disabled()
        page.screenshot(path=str(output / "privacy-390-lost-acknowledgement.png"), full_page=True)
        page.get_by_role("button", name="Refresh requests", exact=True).click()
        expect(page.get_by_text("An export request is already pending.", exact=False)).to_be_visible()
        assert len(state["writes"]) == 1 and state["gets"] == 2
        assert_clean(base)
        context.close()
        print("PASS lost acknowledgement disables resubmission until manual status check")

        context, page, state, base = privacy_context(browser, processing=False)
        state["requests"] = [{"id": EXPORT_ID, "kind": "export", "state": "blocked", "created_at": STAMP, "download_ready": False, "error_code": "processor_unconfigured", "message": "Operator setup required; no processing has occurred."}]
        open_account(page)
        expect(page.get_by_text("Processing is unavailable or not configured.", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Request data export", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Review account erasure", exact=True)).to_be_disabled()
        expect(page.get_by_text("Operator setup required; no processing has occurred.", exact=False)).to_be_visible()
        expect(page.get_by_text("processor_unconfigured", exact=True)).to_be_visible()
        assert not page.get_by_role("button", name="Download account export", exact=True).count()
        page.screenshot(path=str(output / "privacy-390-processing-unconfigured.png"), full_page=True)
        state["requests"][0].update(state="failed", message="Synthetic processing failure. Contact the operator.")
        page.get_by_role("button", name="Refresh requests", exact=True).click()
        expect(page.get_by_text("The service reports this request failed.", exact=False)).to_be_visible()
        page.screenshot(path=str(output / "privacy-390-request-failed.png"), full_page=True)
        assert len(state["writes"]) == 0
        assert_clean(base)
        context.close()
        print("PASS unconfigured/blocked/failed states show operator recovery without auto-retry")

        context, page, state, base = privacy_context(browser, verified=False)
        open_account(page)
        expect(page.get_by_role("button", name="Review account erasure", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Request data export", exact=True)).to_be_enabled()
        assert not state["writes"]
        assert_clean(base)
        context.close()
        print("PASS erasure requires verified session email; no manually supplied identity")

        context, page, state, base = privacy_context(browser, signed_in=False)
        page.goto(fixture.BASE)
        page.get_by_role("button", name="Need export or erasure? Open privacy sign-in", exact=True).click()
        expect(page.get_by_role("heading", name="Sign in to manage account privacy.", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Continue with email", exact=True)).to_be_visible()
        assert state["gets"] == 0 and not state["writes"]
        assert_clean(base)
        context.close()
        print("PASS signed-out privacy entry sends no email or request implicitly")

        context, page, state, base = privacy_context(browser, unpaid=True, fail_status=True)
        open_account(page, unpaid=True)
        expect(page.get_by_role("alert")).to_contain_text("session expired")
        expect(page.get_by_role("button", name="Request data export", exact=True)).to_be_disabled()
        page.screenshot(path=str(output / "privacy-390-session-expired.png"), full_page=True)
        state["fail_status"] = False
        page.get_by_role("button", name="Refresh requests", exact=True).click()
        expect(page.get_by_role("button", name="Request data export", exact=True)).to_be_enabled()
        assert state["gets"] == 2 and not state["writes"]
        assert_clean(base)
        context.close()
        print("PASS privacy load failure recovers manually without workspace entitlement")

        for request_state in ["queued", "processing"]:
            context, page, state, base = privacy_context(browser, processing=False, unpaid=True)
            state["requests"] = [{"id": ERASE_ID, "kind": "erase", "state": request_state, "created_at": STAMP, "completed_at": None, "download_ready": False, "message": "Previously accepted erasure; worker heartbeat is now stale. Inspect this request with the operator."}]
            open_account(page, unpaid=True)
            expect(page.get_by_text("Existing requests may still be queued or processing", exact=False)).to_be_visible()
            expect(page.get_by_text("This status does not establish whether erasure has started or finished.", exact=False)).to_be_visible()
            expect(page.get_by_text("no deletion has been started by this page", exact=False)).to_have_count(0)
            expect(page.get_by_role("button", name="Review account erasure", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="Request data export", exact=True)).to_be_disabled()
            expect(page.locator(".account-request-list")).to_contain_text(request_state)
            assert not state["writes"]
            page.screenshot(path=str(output / f"privacy-390-stale-heartbeat-{request_state}.png"), full_page=True)
            assert_clean(base); context.close()
            print(f"PASS stale worker heartbeat with prior {request_state} erasure does not deny or cancel its acceptance")

        context, page, state, base = privacy_context(browser, unpaid=True)
        state["requests"] = [{"id": EXPORT_ID, "kind": "export", "state": "complete", "created_at": STAMP, "completed_at": STAMP, "download_ready": True, "message": "Private first-owner history fixture."}]
        open_account(page, unpaid=True)
        expect(page.get_by_text("Private first-owner history fixture.", exact=False)).to_be_visible()
        page.get_by_role("button", name="Review account erasure", exact=True).click()
        page.get_by_role("textbox", name="Type DELETE MY ACCOUNT", exact=True).fill("DELETE MY ACCOUNT")
        page.get_by_role("checkbox", name="I understand this queues", exact=False).check()
        expect(page.get_by_role("button", name="Queue erasure request", exact=True)).to_be_enabled()
        state["requests"] = []
        # Only synthetic fixture storage is read. Exercise the real Supabase auth
        # event listener and React app remount, without any hosted auth request.
        page.evaluate("""() => {
          const key = 'sb-pursuit-ui-test-auth-token';
          const session = JSON.parse(localStorage.getItem(key));
          session.user = {...session.user, id: '00000000-0000-4000-8000-000000000099', email: 'second-owner@example.test'};
          const segment = btoa(JSON.stringify({sub: session.user.id, exp: session.expires_at, role: 'authenticated'})).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
          session.access_token = session.access_token.split('.')[0] + '.' + segment + '.fixture';
          localStorage.setItem(key, JSON.stringify(session));
          const channel = new BroadcastChannel(key);
          channel.postMessage({event: 'SIGNED_IN', session});
          channel.close();
        }""")
        expect(page.get_by_role("heading", name="Let’s reconnect.", exact=True)).to_be_visible()
        expect(page.get_by_text("Private first-owner history fixture.", exact=False)).to_have_count(0)
        expect(page.get_by_role("textbox", name="Type DELETE MY ACCOUNT", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Account privacy", exact=True).click()
        expect(page.get_by_text("second-owner@example.test", exact=True)).to_be_visible()
        expect(page.get_by_text("No privacy requests were returned.", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Download account export", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="Queue erasure request", exact=True)).to_have_count(0)
        expect(page.get_by_text("alex@example.test", exact=True)).to_have_count(0)
        assert not state["writes"]
        page.screenshot(path=str(output / "privacy-390-account-switch.png"), full_page=True)
        assert_clean(base); context.close()
        print("PASS synthetic auth account switch clears old history, download actions, confirmation phrase and acknowledgement")
        browser.close()
    (output / "results.json").write_text(json.dumps({"fixture_only": True, "passed": 12, "live_services": False, "screenshots": sorted(path.name for path in output.glob("*.png"))}, indent=2) + "\n")
    print("12 isolated account-privacy browser scenarios passed; all API/auth responses were fixtures.")


if __name__ == "__main__":
    main()
