"""Offline browser checks for the web UI. No hosted auth/database requests.

Start Vite with the synthetic values documented in docs/web-launch-readiness.md.
All non-loopback requests are intercepted. These checks are NOT hosted RLS QA.
"""
import argparse
import base64
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

USER_ID = "00000000-0000-4000-8000-000000000001"
PROFILE = {"user_id": USER_ID, "display_name": "Alex Example", "phone": None,
           "base_location": "Dubai", "onboarding_completed_at": "2026-09-01T12:00:00Z"}
PREFERENCES = {"user_id": USER_ID, "target_titles": ["Accountant"], "preferred_locations": ["Dubai"],
               "preferred_regions": [], "remote_preference": "open", "sponsorship_required": True,
               "work_authorization_notes": None}
JOBS = [{"id": f"job-{index}", "user_id": USER_ID, "source": "manual", "source_url": "https://example.com/careers",
         "title": title, "company_name": company, "location_text": "Dubai", "workplace_type": "hybrid",
         "eligibility_status": eligibility, "status": status, "last_validated_at": None}
        for index, title, company, eligibility, status in [
            (1, "Senior Accountant", "Example Finance", "needs_review", "ready"),
            (2, "Financial Analyst", "Example Company", "eligible", "ready"),
            (3, "Finance Manager", "Sample Employer", "unknown", "new"),
        ]]
APPLICATIONS = [{"id": f"app-{index}", "user_id": USER_ID, "job_id": f"job-{index}",
                 "status": status, "applied_at": None, "updated_at": "2026-09-01T12:00:00Z"}
                for index, status in [(1, "draft"), (2, "submitted"), (3, "interviewing")]]


def session():
    expires = int(time.time()) + 7200
    encode = lambda data: base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    # Intentionally unsigned synthetic session, accepted only by this UI mock.
    token = f'{encode({"alg": "HS256", "typ": "JWT"})}.{encode({"sub": USER_ID, "exp": expires, "role": "authenticated"})}.synthetic'
    return {"access_token": token, "refresh_token": "synthetic-refresh", "expires_at": expires,
            "expires_in": 7200, "token_type": "bearer", "user": {
                "id": USER_ID, "email": "alex@example.test", "aud": "authenticated", "role": "authenticated",
                "app_metadata": {}, "user_metadata": {}, "created_at": "2026-09-01T12:00:00Z"}}


def mock_context(browser, width, height, scheme, *, signed_in=True, fail_load=False):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme, reduced_motion="reduce")
    events = []

    def route_request(route):
        request = route.request
        url = urlsplit(request.url)
        if url.hostname == "127.0.0.1":
            if url.path == "/api" or url.path.startswith("/api/"):
                events.append("Unexpected local API request blocked")
                route.abort()
                return
            route.continue_()
            return
        if url.hostname != "pursuit-ui-test.supabase.co":
            events.append(f"unexpected external request: {url.hostname}")
            route.abort()
            return
        headers = {"access-control-allow-origin": "*", "access-control-allow-headers": "*"}
        if request.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
        elif url.path == "/auth/v1/otp":
            route.fulfill(status=429, headers=headers, json={"code": "over_email_send_rate_limit", "msg": "Email rate limit exceeded"})
        elif url.path == "/auth/v1/logout":
            route.fulfill(status=204, headers=headers)
        elif url.path.startswith("/rest/v1/"):
            table = url.path.rsplit("/", 1)[-1]
            if fail_load:
                route.fulfill(status=503, headers=headers, json={"message": "Synthetic outage"})
                return
            rows = {"profiles": [PROFILE], "job_preferences": [PREFERENCES], "jobs": JOBS,
                    "applications": APPLICATIONS, "resumes": [], "artifacts": []}.get(table, [])
            if request.method != "GET":
                events.append(f"write:{table}")
            if "application/vnd.pgrst.object+json" in request.headers.get("accept", ""):
                rows = rows[0] if rows else None
            route.fulfill(status=200, headers=headers, json=rows)
        else:
            events.append(f"unexpected mocked endpoint: {url.path}")
            route.abort()

    context.route("**/*", route_request)
    if signed_in:
        context.add_init_script("localStorage.setItem('sb-pursuit-ui-test-auth-token', " + json.dumps(json.dumps(session())) + ");")
    page = context.new_page()
    page.on("pageerror", lambda error: events.append(f"pageerror: {error}"))
    return context, page, events


def no_overflow(page):
    if not page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"):
        offenders = page.evaluate("Array.from(document.querySelectorAll('main *')).filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => [e.tagName, e.className, Math.round(e.getBoundingClientRect().right)]).slice(0, 20)")
        raise AssertionError(f"Horizontal page overflow at {page.viewport_size}: {offenders}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5178/beta")
    parser.add_argument("--output", default="web/node_modules/.cache/pursuit-ui-review")
    args = parser.parse_args()
    if urlsplit(args.url).hostname != "127.0.0.1":
        parser.error("Only a loopback UI preview is allowed.")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for scheme in ["light", "dark"]:
            for width, height in [(1440, 1000), (900, 1000), (390, 844), (320, 740)]:
                context, page, events = mock_context(browser, width, height, scheme)
                page.goto(args.url)
                expect(page.get_by_role("heading", name="Your next move, Alex.")).to_be_visible()
                no_overflow(page)
                if width in (1440, 390):
                    page.screenshot(path=str(output / f"today-{width}-{scheme}.png"), full_page=width >= 1000)
                opportunity = page.locator(".pursuit-opportunity").first
                opportunity.click()
                expect(page.get_by_role("dialog")).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.get_by_role("dialog")).to_have_count(0)
                expect(opportunity).to_be_focused()
                nav = page.get_by_role("navigation", name="Primary navigation")
                expect(nav.get_by_role("button")).to_have_count(4)
                nav.get_by_role("button", name="Discover", exact=True).click()
                expect(page.locator(".beta-job-list .beta-job")).to_have_count(3)
                no_overflow(page)
                nav.get_by_role("button", name="Tracker", exact=True).click()
                expect(page.locator(".beta-applications-workspace-row")).to_have_count(3)
                page.get_by_role("button", name="Submitted", exact=True).click()
                expect(page.locator(".beta-applications-workspace-row")).to_have_count(1)
                expect(page.locator(".beta-applications-workspace-row h3")).to_have_text("Financial Analyst")
                no_overflow(page)
                nav.get_by_role("button", name="Studio", exact=True).click()
                expect(page.get_by_role("heading", name="Your experience, in focus.")).to_be_visible()
                expect(page.get_by_text("AI tailoring, interview coaching", exact=False)).to_be_visible()
                no_overflow(page)
                page.get_by_role("button", name="Your profile", exact=True).click()
                page.get_by_role("button", name="Edit profile and preferences").click()
                expect(page.get_by_role("textbox", name="Name")).to_have_value("Alex Example")
                page.get_by_role("textbox", name="Name").press("Enter")
                expect(page.get_by_role("heading", name="What work are you looking for?")).to_be_visible()
                no_overflow(page)
                assert not events, events
                context.close()
                print(f"PASS signed-in UI {width}px {scheme}")

            for width, height in [(1440, 1000), (390, 844)]:
                context, page, events = mock_context(browser, width, height, scheme, signed_in=False)
                page.goto(args.url)
                expect(page.get_by_role("button", name="Create account with email")).to_be_visible()
                no_overflow(page)
                page.screenshot(path=str(output / f"signin-{width}-{scheme}.png"), full_page=True)
                page.get_by_role("textbox", name="Email address").fill("alex@example.test")
                page.get_by_role("button", name="Create account with email").click()
                expect(page.get_by_role("alert")).to_contain_text("temporarily rate-limited")
                expect(page.get_by_role("button", name="Create account with email")).to_be_disabled()
                assert not events, events
                context.close()
                print(f"PASS sign-in + mocked rate limit {width}px {scheme}")

        context, page, events = mock_context(browser, 390, 844, "light", fail_load=True)
        page.goto(args.url)
        # Supabase may retry a 503; the UI bounds the complete workspace load at 15s.
        expect(page.get_by_role("heading", name="Let’s reconnect.")).to_be_visible(timeout=20000)
        expect(page.get_by_role("button", name="Try again")).to_be_visible()
        expect(page.get_by_role("heading", name="Let’s start with the essentials.")).to_have_count(0)
        assert not events, events
        context.close()
        browser.close()
        print("PASS workspace failure shows retry, not onboarding")
        print("13 mock browser scenarios passed. No live backend or emails used.")


if __name__ == "__main__":
    main()
