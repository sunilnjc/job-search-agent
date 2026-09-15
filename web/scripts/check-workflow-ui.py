"""Mocked React UI checks only; main's real ASGI browser check covers integration.

Run after: node scripts/build-workflow-fixture.mjs --serve
All API requests are synthetic fixtures. Every non-preview request is intercepted.
No real user, Supabase, provider, storage, email, application or hosted mutation.
"""
import base64
import copy
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
expect.set_options(timeout=20000)

BASE = "http://127.0.0.1:5187/beta"
UID = "00000000-0000-4000-8000-000000000001"
JID = "00000000-0000-4000-8000-000000000002"
RID = "00000000-0000-4000-8000-000000000003"
AID = "00000000-0000-4000-8000-000000000004"
PROFILE = {"user_id": UID, "display_name": "Alex Fixture", "base_location": "Dubai", "phone": None,
           "onboarding_completed_at": "2026-09-01T12:00:00Z", "career_text": "Confirmed synthetic design experience."}
PREFS = {"user_id": UID, "target_titles": ["Designer"], "preferred_locations": [], "preferred_regions": [],
         "remote_preference": "open", "sponsorship_required": False, "work_authorization_notes": None}
JOB = {"id": JID, "source": "manual", "source_url": "https://example.invalid/careers/designer", "title": "Product Designer",
       "company_name": "Fictional Studio", "location_text": "Dubai", "workplace_type": "hybrid", "status": "new",
       "eligibility_status": "unknown", "last_validated_at": None, "description": "Synthetic employer posting: design accessible products. Three years of design experience."}
RESUME = {"id": RID, "label": "Fixture resume", "original_filename": "fixture.pdf", "byte_size": 123, "is_default": True}
ARTIFACT = {"id": AID, "job_id": JID, "resume_id": RID, "kind": "tailored_resume", "filename": "role-resume.pdf", "byte_size": 42,
            "mime_type": "application/pdf", "created_at": "2026-09-01T12:00:00Z"}


def context_for(browser, width=1440, scheme="light", *, onboarding=False, fail_marker=False, signed_in=True, verified_email=True):
    expires = int(time.time()) + 7200
    encode = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    token = f'{encode({"alg": "HS256", "typ": "JWT"})}.{encode({"sub": UID, "exp": expires, "role": "authenticated"})}.fixture'
    session = {"access_token": token, "refresh_token": "fixture-refresh", "expires_at": expires, "expires_in": 7200,
               "token_type": "bearer", "user": {"id": UID, "email": "alex@example.test", "aud": "authenticated", "role": "authenticated",
               "app_metadata": {}, "user_metadata": {}, "email_confirmed_at": "2026-09-01T12:00:00Z" if verified_email else None, "created_at": "2026-09-01T12:00:00Z"}}
    data = {"profile": None if onboarding else copy.deepcopy(PROFILE), "preferences": None if onboarding else copy.deepcopy(PREFS),
            "jobs": [copy.deepcopy(JOB)], "resumes": [] if onboarding else [copy.deepcopy(RESUME)], "artifacts": [], "applications": [],
            "questions": [], "capabilities": {"job_detail_fetch": True, "bootstrap_list_limit": 200}}
    state = {"data": data, "writes": [], "unexpected": [], "pending": [], "fail_marker": fail_marker,
             "fail_prefs": onboarding and not fail_marker, "rank_calls": 0, "prepare_calls": 0, "uploads": 0}
    context = browser.new_context(viewport={"width": width, "height": 950 if width > 600 else 844}, color_scheme=scheme, reduced_motion="reduce", service_workers="block")
    if signed_in:
        context.add_init_script("localStorage.setItem('sb-pursuit-ui-test-auth-token', " + json.dumps(json.dumps(session)) + ");")

    def intercept(route):
        req = route.request
        url = urlsplit(req.url)
        if url.hostname == "127.0.0.1" and url.port == 5187 and not url.path.startswith("/api"):
            route.continue_()
            return
        if url.hostname != "127.0.0.1" or url.port != 5187 or not url.path.startswith("/api/mobile/"):
            state["unexpected"].append((req.method, url.hostname, url.path))
            route.abort()
            return
        assert req.headers.get("authorization") == "Bearer " + token
        path = url.path.removeprefix("/api/mobile")
        body = req.post_data_json if req.post_data else {}
        if req.method != "GET":
            state["writes"].append((req.method, path, body))
        if path == "/bootstrap":
            compact = copy.deepcopy(data)
            compact["jobs"] = [{**job, "description": None} for job in data["jobs"]]
            route.fulfill(json=compact)
        elif path == f"/jobs/{JID}" and req.method == "GET":
            route.fulfill(json=data["jobs"][0])
        elif path == "/resume-operations":
            route.fulfill(json=[])
        elif path == "/artifact-operations":
            route.fulfill(json=state["pending"])
        elif path == "/profile":
            if body.get("onboarding_completed_at") and state["fail_marker"]:
                state["fail_marker"] = False
                route.fulfill(status=503, json={"detail": "Synthetic completion-marker failure; draft retained."})
                return
            data["profile"] = {"user_id": UID, **(data["profile"] or {}), **body}
            route.fulfill(json=data["profile"])
        elif path == "/preferences":
            if state["fail_prefs"]:
                state["fail_prefs"] = False
                route.fulfill(status=503, json={"detail": "Synthetic preferences failure; draft retained."})
                return
            data["preferences"] = {"user_id": UID, **body}
            route.fulfill(json=data["preferences"])
        elif path == "/resumes" and req.method == "POST":
            assert req.headers.get("idempotency-key")
            state["uploads"] += 1
            data["resumes"].append({**RESUME, "original_filename": body["filename"]})
            route.fulfill(status=201, json=data["resumes"][-1])
        elif path == f"/jobs/{JID}/rank":
            state["rank_calls"] += 1
            data["jobs"][0].update(score=7.5, rationale="Synthetic fixture estimate.")
            route.fulfill(json={**data["jobs"][0], "operation_status": "saved_sync_pending", "warnings": ["Your ranking is saved. Activity log sync is pending; refresh before reassessment."]})
        elif path == f"/jobs/{JID}/prepare":
            state["prepare_calls"] += 1
            state["pending"] = [{"id": AID, "job_id": JID, "filename": ARTIFACT["filename"], "state": "upload_pending"}]
            route.fulfill(status=503, json={"detail": {"code": "artifact_recovery_required", "operation_id": AID, "saved_artifacts": [], "message": "Synthetic interrupted artifact save. Recover retained bytes."}})
        elif path == f"/artifact-operations/{AID}/recover":
            state["pending"] = []
            data["artifacts"] = [copy.deepcopy(ARTIFACT)]
            route.fulfill(json=ARTIFACT)
        elif path == f"/artifacts/{AID}/download":
            route.fulfill(body=b"synthetic document bytes", content_type="application/pdf")
        else:
            state["unexpected"].append((req.method, "unmocked-api", path))
            route.fulfill(status=501, json={"detail": "Not in this bounded fixture."})

    context.route("**/*", intercept)
    page = context.new_page()
    page.set_default_timeout(20000)
    page.on("pageerror", lambda error: state["unexpected"].append(("pageerror", str(error))))
    return context, page, state


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Page horizontal overflow"
    for panel in page.locator(".beta-studio-modal, .workflow-card").all():
        assert panel.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), "Panel horizontal overflow"


def open_studio(page):
    page.goto(BASE)
    expect(page.get_by_role("heading", name="Your next move, Alex.")).to_be_visible()
    page.get_by_role("navigation", name="Primary navigation").get_by_role("button", name="Discover", exact=True).click()
    page.get_by_role("button", name="Application studio", exact=True).click()
    expect(page.get_by_role("textbox", name="Full job description", exact=True)).to_have_value(JOB["description"])


def onboarding_to_review(page, *, upload=False):
    page.goto(BASE)
    page.get_by_role("textbox", name="Name", exact=True).fill("Draft Fixture")
    page.get_by_role("button", name="Continue", exact=True).click()
    page.get_by_role("textbox", name="Target roles", exact=False).fill("Designer")
    page.get_by_role("button", name="Continue", exact=True).click()
    page.get_by_role("button", name="Continue", exact=True).click()
    if upload:
        page.get_by_label("Onboarding resume", exact=True).set_input_files({"name": "fixture.pdf", "mimeType": "application/pdf", "buffer": b"mocked validation only"})
    page.get_by_role("button", name="Continue", exact=True).click()


def main():
    output = Path(__file__).resolve().parents[1] / "node_modules/.cache/launch-web-visual"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for scheme in ["light", "dark"]:
            for width in [320, 390, 900, 1440]:
                context, page, state = context_for(browser, width, scheme)
                open_studio(page)
                assert not state["writes"], "Opening Studio must be read-only"
                no_overflow(page)
                page.get_by_role("button", name="Documents", exact=True).click()
                expect(page.get_by_role("heading", name="Assess and prepare")).to_be_visible()
                expect(page.get_by_role("button", name="Prepare draft documents")).to_be_disabled()
                no_overflow(page)
                if width in (390, 1440):
                    page.screenshot(path=str(output / f"studio-{width}-{scheme}.png"))
                page.keyboard.press("Escape")
                expect(page.get_by_role("dialog")).to_have_count(0)
                expect(page.get_by_role("button", name="Application studio", exact=True)).to_be_focused()
                assert not state["unexpected"], state["unexpected"]
                context.close()
                print(f"PASS mock visual/focus/consent {width}px {scheme}")

        context, page, state = context_for(browser)
        open_studio(page)
        page.get_by_role("button", name="Documents", exact=True).click()
        page.get_by_role("checkbox", name="Enable AI assistance", exact=False).check()
        page.get_by_role("button", name="Assess role with AI").click()
        expect(page.get_by_text("Your ranking is saved. Activity log sync is pending; refresh before reassessment.", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Prepare draft documents")).to_be_disabled()
        assert state["rank_calls"] == 1 and state["prepare_calls"] == 0
        page.get_by_role("button", name="Refresh saved Studio").click()
        expect(page.get_by_role("button", name="Prepare draft documents")).to_be_enabled()
        page.get_by_role("button", name="Prepare draft documents").click()
        expect(page.get_by_role("button", name="Recover saved artifact", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Prepare draft documents")).to_be_disabled()
        page.get_by_role("button", name="Recover saved artifact", exact=True).click()
        expect(page.get_by_role("button", name="Download document", exact=True)).to_be_visible()
        expect(page.get_by_text("Tailored resume · PDF", exact=True)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("button", name="Download document", exact=True).click()
        assert download.value.suggested_filename == ARTIFACT["filename"]
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_text("Tailored resume · PDF", exact=True).scroll_into_view_if_needed()
        no_overflow(page)
        page.screenshot(path=str(output / "documents-friendly-390.png"))
        assert state["rank_calls"] == 1 and state["prepare_calls"] == 1
        assert not state["unexpected"], state["unexpected"]
        context.close()
        print("PASS mock saved-sync warning and recovery do not regenerate")

        for fail_marker in [False, True]:
            context, page, state = context_for(browser, 390, onboarding=True, fail_marker=fail_marker)
            onboarding_to_review(page, upload=fail_marker)
            page.get_by_role("button", name="Create my private workspace", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("Synthetic")
            assert not state["data"]["profile"].get("onboarding_completed_at")
            page.reload()
            expect(page.get_by_role("heading", name="Review", exact=True)).to_be_visible()
            expect(page.get_by_text("Draft Fixture", exact=True)).to_be_visible()
            no_overflow(page)
            page.get_by_role("button", name="Create my private workspace", exact=True).click()
            expect(page.get_by_role("heading", name="Your next move, Draft.")).to_be_visible()
            assert state["uploads"] == (1 if fail_marker else 0), "Confirmed upload must not repeat"
            assert not page.evaluate("sessionStorage.getItem('job-pursuit:onboarding:v1:' + " + json.dumps(UID) + ")")
            assert not state["unexpected"], state["unexpected"]
            context.close()
            print("PASS mock onboarding reload and " + ("saved-upload receipt" if fail_marker else "preferences-failure draft"))
        browser.close()
    print("11 mocked UI scenarios passed. No real ASGI/provider/Supabase or network writes.")


if __name__ == "__main__":
    main()
