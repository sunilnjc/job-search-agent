"""Mocked React UI checks only; main's real ASGI browser check covers integration.

Run after: node scripts/build-workflow-fixture.mjs --serve
All API requests are synthetic fixtures. Every non-preview request is intercepted.
No real user, Supabase, provider, storage, email, application or hosted mutation.
"""
import base64
import copy
import json
import re
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
LETTER_ID = "00000000-0000-4000-8000-000000000005"
RUN_ID = "00000000-0000-4000-8000-000000000006"
APP_ID = "00000000-0000-4000-8000-000000000007"
REVIEW_ID = "00000000-0000-4000-8000-000000000008"
STAMP = "2026-09-15T00:00:00Z"
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


def empty_readiness(job_id):
    # Exact HTTP contract from readiness.py / checkedReadiness. This fixture is
    # not a SQL/byte-integrity proof and never infers readiness from filenames.
    return {"user_id": UID, "job_id": job_id, "version": "packet-v1", "ready": False,
        "reason": "eligibility_required", "pending_questions": 0, "application_status": "draft",
        "recorded_status": None, "application_id": None, "packets": [], "review": None}


def seed_server_packet(state):
    """A deliberately explicit server packet for isolated UI contract tests."""
    letter = {**ARTIFACT, "id": LETTER_ID, "kind": "cover_letter", "filename": "role-cover-letter.pdf"}
    pair = [{**ARTIFACT, "sha256": "a" * 64}, {**letter, "sha256": "b" * 64}]
    packet = {"key": RUN_ID + ":pdf", "run_id": RUN_ID, "resume_id": RID, "format": "pdf",
        "variant": "role_aligned", "generated_at": STAMP, "current": True, "issue": None,
        "packet_fingerprint": "c" * 64, "context_fingerprint": "d" * 64,
        "source_sha256": "e" * 64, "artifacts": pair}
    state["data"]["artifacts"] = copy.deepcopy(pair)
    state["data"]["jobs"][0]["eligibility_review"] = {"status": "eligible", "confirmed": True, "reason": "Synthetic self-report for this fixture posting."}
    state["readiness"][JID] = {**empty_readiness(JID), "reason": "packet_review_required", "packets": [packet]}
    return packet


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
             "fail_prefs": onboarding and not fail_marker, "rank_calls": 0, "prepare_calls": 0, "uploads": 0,
             "readiness": {}, "readiness_reads": [], "review_calls": [], "readiness_mode": "ok",
             "document_review_reads": [], "document_review_mode": "ok"}
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
        if req.method != "GET" and path != "/discovery/search":
            state["writes"].append((req.method, path, body))
        if path == "/discovery/search":
            route.fulfill(json={"status": "ok", "results": [], "sources": [], "partial": False, "truncated": False,
                "matched_count": 0, "returned_count": 0, "searched_at": "2026-09-15T00:00:00Z", "persisted": False, "eligibility_verified": False})
        elif path == "/bootstrap":
            compact = copy.deepcopy(data)
            compact["jobs"] = [{**job, "description": None} for job in data["jobs"]]
            route.fulfill(json=compact)
        elif path == f"/jobs/{JID}" and req.method == "GET":
            route.fulfill(json=data["jobs"][0])
        elif re.fullmatch(r"/jobs/[0-9a-f-]{36}/document-reviews", path) and req.method == "GET":
            job_id = path.split("/")[2]
            assert any(job["id"] == job_id for job in data["jobs"]), "Evidence reads require a saved job UUID"
            state["document_review_reads"].append(job_id)
            mode = state["document_review_mode"]
            if mode == "missing":
                route.fulfill(status=404, json={"detail": "Not Found"})
            elif mode == "not_acceptable":
                route.fulfill(status=406, json={"detail": "Synthetic saved document review request was not acceptable."})
            else:
                route.fulfill(json={"reviews": [], "unavailable_count": 1 if mode == "inconsistent" else 0})
        elif re.fullmatch(r"/jobs/[0-9a-f-]{36}/readiness", path) and req.method == "GET":
            job_id = path.split("/")[2]
            assert any(job["id"] == job_id for job in data["jobs"]), "Readiness must use a saved job UUID"
            state["readiness_reads"].append(job_id)
            if state["readiness_mode"] == "unavailable":
                route.fulfill(status=503, json={"detail": {"code": "readiness_unavailable", "message": "Synthetic packet verification unavailable. Refresh before reviewing."}})
                return
            readiness = copy.deepcopy(state["readiness"].get(job_id, empty_readiness(job_id)))
            if state["readiness_mode"] == "foreign":
                readiness["user_id"] = "00000000-0000-4000-8000-000000000099"
            route.fulfill(json=readiness)
        elif path == f"/jobs/{JID}/review-packet" and req.method == "POST":
            state["review_calls"].append(copy.deepcopy(body))
            assert set(body) == {"run_id", "resume_artifact_id", "letter_artifact_id", "packet_fingerprint", "confirmed"}
            current = state["readiness"][JID]
            packet = next((p for p in current["packets"] if p["run_id"] == body["run_id"]
                and p["current"] and p["packet_fingerprint"] == body["packet_fingerprint"]
                and p["artifacts"][0]["id"] == body["resume_artifact_id"]
                and p["artifacts"][1]["id"] == body["letter_artifact_id"]), None)
            if state["readiness_mode"] == "conflict_on_review" or not packet:
                route.fulfill(status=409, json={"detail": "Synthetic packet changed after your refresh. Review the current generation."})
                return
            assert body["confirmed"] is True and current["pending_questions"] == 0
            assert current["reason"] not in ("eligibility_required", "pending_questions", "too_many_records")
            app = {"id": APP_ID, "job_id": JID, "status": "ready", "notes": None, "applied_at": None, "created_at": STAMP, "updated_at": STAMP}
            current.update(ready=True, reason=None, application_status="ready", recorded_status="ready", application_id=APP_ID,
                review={"id": REVIEW_ID, "run_id": body["run_id"], "resume_artifact_id": body["resume_artifact_id"],
                    "letter_artifact_id": body["letter_artifact_id"], "packet_fingerprint": body["packet_fingerprint"], "current": True, "reviewed_at": STAMP})
            data["applications"] = [{**app, "readiness": copy.deepcopy(current), "recorded_status": "ready"}]
            data["jobs"][0].update(status="ready", readiness=copy.deepcopy(current), application_status="ready")
            route.fulfill(json={"application": app, "readiness": current})
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
        elif path in (f"/artifacts/{AID}/download", f"/artifacts/{LETTER_ID}/download"):
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
    page.get_by_role("button", name=re.compile(r"^Saved roles \(")).click()
    page.get_by_role("button", name="Application studio", exact=True).click()
    expect(page.get_by_role("textbox", name="Full job description", exact=True)).to_have_value(JOB["description"])


def select_strict_countries(page):
    page.get_by_role("combobox", name=re.compile(r"^Work preference")).select_option("remote_only")
    page.get_by_role("checkbox", name=re.compile(r"^I need visa sponsorship")).check()
    page.get_by_role("combobox", name=re.compile(r"^Remote location rule")).select_option("require_explicit")
    country = page.get_by_role("combobox", name=re.compile(r"^Add a country for remote work"))
    country.select_option(label="Germany")
    country.select_option(label="United Arab Emirates")
    country.select_option(label="Germany")
    selected = page.get_by_role("list", name="Selected remote countries", exact=True)
    expect(selected.get_by_role("listitem")).to_have_count(2)
    expect(selected).to_contain_text("Germany")
    expect(selected).to_contain_text("United Arab Emirates")
    page.get_by_role("button", name="Remove Germany", exact=True).click()
    expect(selected.get_by_role("listitem")).to_have_count(1)
    country.select_option(label="Germany")
    expect(selected.get_by_role("listitem")).to_have_count(2)
    page.get_by_role("combobox", name=re.compile(r"^Sponsorship rule")).select_option("require_explicit")
    no_overflow(page)


def onboarding_to_review(page, *, upload=False, strict=False):
    page.goto(BASE)
    page.get_by_role("textbox", name="Name", exact=True).fill("Draft Fixture")
    page.get_by_role("textbox", name=re.compile("^Confirmed career facts")).fill("Product Designer at Fictional Studio, 2022-present. Built accessible design systems.")
    page.get_by_role("checkbox", name=re.compile("reviewed these facts")).check()
    page.get_by_role("button", name="Continue", exact=True).click()
    page.get_by_role("textbox", name="Target roles", exact=False).fill("Designer")
    page.get_by_role("button", name="Continue", exact=True).click()
    if strict:
        select_strict_countries(page)
    page.get_by_role("button", name="Continue", exact=True).click()
    if upload:
        page.get_by_label("Onboarding resume", exact=True).set_input_files({"name": "fixture.pdf", "mimeType": "application/pdf", "buffer": b"mocked validation only"})
    page.get_by_role("button", name="Continue", exact=True).click()


def assert_strict_preferences(preferences):
    assert preferences["remote_preference"] == "remote_only"
    assert preferences["sponsorship_required"] is True
    rules = preferences["discovery_rules"]
    assert rules["remote_country_policy"] == rules["sponsorship_policy"] == "require_explicit"
    assert sorted(rules["remote_country_codes"]) == ["AE", "DE"]


def final_review(page, packet=None):
    page.get_by_role("button", name="Final review", exact=True).click()
    ready = page.get_by_role("button", name="Mark ready for manual apply", exact=True)
    expect(ready).to_be_disabled()
    if packet:
        selector = page.get_by_role("combobox", name=re.compile(r"^Document packet"))
        expect(selector).to_have_value("")
        selector.select_option(packet["key"])
        expect(ready).to_be_disabled()
    return ready


def review_checkbox(page):
    return page.get_by_role("checkbox", name=re.compile(r"^I downloaded and reviewed the selected same-generation"))


def review_counts(state, expected):
    assert len(state["review_calls"]) == expected
    assert len(state["writes"]) == expected, "Ready must only POST review-packet; never write applications/status directly"
    assert state["rank_calls"] == state["prepare_calls"] == 0
    assert not state["unexpected"], state["unexpected"]


def main():
    output = Path(__file__).resolve().parents[1] / "node_modules/.cache/launch-web-visual"
    output.mkdir(parents=True, exist_ok=True)
    evidence = []
    def passed(message):
        evidence.append(message)
        print("PASS " + message, flush=True)
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
                expect(page.get_by_role("region", name="Document evidence review")).to_contain_text("No source review was saved for this job yet.")
                assert state["document_review_reads"] == [JID], "Opening Documents reads evidence but never generates it"
                expect(page.get_by_role("button", name="Prepare draft documents")).to_be_disabled()
                no_overflow(page)
                if width in (390, 1440):
                    page.screenshot(path=str(output / f"studio-{width}-{scheme}.png"))
                page.keyboard.press("Escape")
                expect(page.get_by_role("dialog")).to_have_count(0)
                expect(page.get_by_role("button", name="Application studio", exact=True)).to_be_focused()
                assert not state["unexpected"], state["unexpected"]
                context.close()
                passed(f"mock visual/focus/consent {width}px {scheme}")

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
        passed("mock saved-sync warning and recovery do not regenerate")

        for fail_marker in [False, True]:
            context, page, state = context_for(browser, 390, onboarding=True, fail_marker=fail_marker)
            onboarding_to_review(page, upload=fail_marker, strict=True)
            page.get_by_role("button", name="Create my private workspace", exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("Synthetic")
            assert not state["data"]["profile"].get("onboarding_completed_at")
            page.reload()
            expect(page.get_by_role("heading", name="Review", exact=True)).to_be_visible()
            expect(page.get_by_text("Draft Fixture", exact=True)).to_be_visible()
            no_overflow(page)
            page.get_by_role("button", name="Create my private workspace", exact=True).click()
            expect(page.get_by_role("heading", name="Roles for your next move.")).to_be_visible()
            assert_strict_preferences(state["data"]["preferences"])
            for _, path, body in state["writes"]:
                if path == "/preferences":
                    assert_strict_preferences(body)
            assert state["uploads"] == (1 if fail_marker else 0), "Confirmed upload must not repeat"
            assert not page.evaluate("sessionStorage.getItem('job-pursuit:onboarding:v1:' + " + json.dumps(UID) + ")")
            assert not state["unexpected"], state["unexpected"]
            context.close()
            passed("mock strict named-country onboarding/reload and " + ("saved-upload receipt" if fail_marker else "preferences-failure draft"))

        # A server-authorized pair is a fixture, not proof of DB/byte validation.
        # These checks establish that React consumes that contract conservatively.
        for width in [390, 1440]:
            context, page, state = context_for(browser, width)
            packet = seed_server_packet(state)
            open_studio(page)
            ready = final_review(page, packet)
            for label, filename in [("resume", "role-resume.pdf"), ("cover letter", "role-cover-letter.pdf")]:
                with page.expect_download() as download:
                    page.get_by_role("button", name="Download selected " + label, exact=True).click()
                assert download.value.suggested_filename == filename
            review_checkbox(page).check()
            expect(ready).to_be_enabled()
            ready.click()
            expect(page.get_by_text("Your ready application record was saved. Nothing was sent to an employer.", exact=True)).to_be_visible()
            expect(page.get_by_text(re.compile(r"^Your review of this exact packet is saved and current"))).to_be_visible()
            assert state["review_calls"] == [{"run_id": RUN_ID, "resume_artifact_id": AID,
                "letter_artifact_id": LETTER_ID, "packet_fingerprint": "c" * 64, "confirmed": True}]
            assert len(state["readiness_reads"]) >= 3, "Opening, click-boundary verification and post-save refresh must read readiness"
            # No new review consent or write should be required after reloading.
            open_studio(page)
            page.get_by_role("button", name="Final review", exact=True).click()
            expect(review_checkbox(page)).to_be_checked()
            expect(review_checkbox(page)).to_be_disabled()
            expect(page.get_by_text(re.compile(r"^Your review of this exact packet is saved and current"))).to_be_visible()
            no_overflow(page)
            page.screenshot(path=str(output / f"durable-server-review-{width}.png"))
            review_counts(state, 1)
            context.close()
            passed(f"server-owned packet downloads/review/receipt/reload {width}px; review POST=1 AI=0")

        for mode in ["stale_at_click", "conflict_on_review", "unavailable", "foreign", "legacy_files"]:
            context, page, state = context_for(browser, 390)
            packet = seed_server_packet(state)
            if mode == "legacy_files":
                # Even convincing legacy same-generation filenames confer no authority.
                for artifact in state["data"]["artifacts"]:
                    artifact["filename"] = artifact["kind"] + "-role_aligned-20260915T000000000000Z-" + RUN_ID + ".pdf"
                state["readiness"][JID]["packets"] = []
            elif mode in ("unavailable", "foreign"):
                state["readiness_mode"] = mode
            open_studio(page)
            ready = final_review(page, packet if mode in ("stale_at_click", "conflict_on_review") else None)
            if mode in ("stale_at_click", "conflict_on_review"):
                review_checkbox(page).check()
                expect(ready).to_be_enabled()
                if mode == "stale_at_click":
                    packet.update(current=False, issue="generation_context_changed")
                else:
                    state["readiness_mode"] = mode
                ready.click()
                expect(page.get_by_role("alert")).to_contain_text("no longer matches" if mode == "stale_at_click" else "Synthetic packet changed")
                expect(ready).to_be_disabled()
            else:
                expect(review_checkbox(page)).to_be_disabled()
                if mode == "legacy_files":
                    expect(page.get_by_text(re.compile(r"^No server-verified generation is available"))).to_be_visible()
                else:
                    expect(page.get_by_role("alert")).to_contain_text("Packet verification unavailable")
                    if mode == "foreign":
                        expect(page.get_by_role("alert")).to_contain_text("invalid packet readiness")
            assert not state["readiness"][JID]["ready"] and not state["data"]["applications"]
            review_counts(state, 1 if mode == "conflict_on_review" else 0)
            no_overflow(page)
            page.screenshot(path=str(output / f"readiness-{mode}-390.png"))
            context.close()
            passed(f"server-owned readiness fail-closed {mode}; no false Ready or automatic retry")

        for mode in ["missing", "not_acceptable", "inconsistent"]:
            context, page, state = context_for(browser, 390)
            state["document_review_mode"] = mode
            state["data"]["artifacts"] = [copy.deepcopy(ARTIFACT)]
            open_studio(page)
            page.get_by_role("button", name="Documents", exact=True).click()
            panel = page.get_by_role("region", name="Document evidence review", exact=True)
            refresh = panel.get_by_role("button", name="Refresh document reviews", exact=True)
            expect(refresh).to_be_enabled()
            if mode == "missing":
                expect(panel.get_by_role("alert")).to_contain_text("This service version does not provide saved document reviews yet")
                expect(panel.get_by_role("alert")).to_contain_text("review their original sources before sharing")
            elif mode == "not_acceptable":
                expect(panel.get_by_role("alert")).to_contain_text("Synthetic saved document review request was not acceptable")
            else:
                expect(panel.get_by_role("status")).to_contain_text("Evidence is unavailable or inconsistent for 1")
                expect(panel.get_by_role("status")).to_contain_text("This is not an all-clear")
            assert state["document_review_reads"] == [JID], "A failed read must not retry automatically"
            # Evidence failures must not strand existing privately saved downloads.
            with page.expect_download() as download:
                page.get_by_role("button", name="Download document", exact=True).click()
            assert download.value.suggested_filename == ARTIFACT["filename"]
            review_counts(state, 0)
            state["document_review_mode"] = "ok"
            refresh.click()
            expect(panel).to_contain_text("No source review was saved for this job yet.")
            expect(panel.get_by_role("alert")).to_have_count(0)
            assert state["document_review_reads"] == [JID, JID]
            review_counts(state, 0)
            no_overflow(page)
            context.close()
            passed(f"document evidence {mode}/explicit refresh recovery; existing download works, GET=2 writes=0 AI=0")
        browser.close()
    (output / "results.json").write_text(json.dumps({"fixture_only": True, "scenarios": evidence,
        "passed": len(evidence), "live_services": False}, indent=2) + "\n")
    print(f"{len(evidence)} mocked UI scenarios passed. No real ASGI/provider/Supabase or network writes.")


if __name__ == "__main__":
    main()
