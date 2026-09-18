"""Repair acceptance: actual React UI -> real ASGI/repository/studio -> fake cloud.

Requires the explicitly synthetic Vite instance on 127.0.0.1:5187. No emails,
external job requests or paid provider calls. Historical audit evidence is kept.
"""
import base64
import argparse
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "scripts")]
import httpx
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from jobagent.mobile import studio
from jobagent.mobile.app import create_app
from jobagent.mobile.discovery import Board, DiscoveryConfig, DiscoveryService
from test_mobile_api import FakeSupabase, SETTINGS, TOKENS, USER_A
from test_mobile_discovery import response as feed_response
from web_ui_smoke import session

OUT = ROOT / "docs/testing/evidence/core-launch-20260915/browser-final"
URL = "http://127.0.0.1:5187/beta"


class EvidenceProvider:
    model_metadata = {"provider": "fixture", "model_name": "repair-evidence-only", "prompt_version": studio.PROMPT_VERSION, "status": "received"}

    def __init__(self):
        self.calls = []

    def complete(self, *, payload, **kwargs):
        operation = payload["operation"]
        self.calls.append(operation)
        facts = [f for f in payload["source_facts"] if f["id"].startswith("career_text.") and studio.claim_allowed(f, documents=True)]
        claims = [{"text": f["text"], "source_ids": [f["id"]]} for f in facts[:4]]
        if operation == "rank":
            return json.dumps({"score": 8.0, "recommendation": "match", "claims": claims[:2], "questions": [], "requirements": []})
        if operation == "documents":
            return json.dumps({"summary": claims[:1], "experience": claims[1:3], "skills": claims[3:4],
                               "education": [], "cover_letter": claims[:3], "questions": [], "requirements": []})
        raise AssertionError("Unexpected provider operation")


def run_persona(browser, persona, width):
    remote, provider = FakeSupabase(), EvidenceProvider()
    auth = session()
    auth["user"].update(id=USER_A, email=persona["email"])
    remote.auth_emails[USER_A] = {"email": persona["email"], "email_confirmed_at": "2026-09-01T00:00:00Z"}
    original = auth["access_token"].split(".")
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    original[1] = encode({"sub": USER_A, "exp": auth["expires_at"], "role": "authenticated"})
    auth["access_token"] = ".".join(original)
    licence_gap = persona["id"] == "P11"
    result = {"persona": persona["id"], "width": width, "boundary": "real React/ASGI/studio; fake Auth/DB/Storage/provider",
        "errors": [], "checks": [], "expected_rejections": [],
        "expected_outcome": "prepared_but_not_ready_missing_NMC_unknown_eligibility" if licence_gap else "explicit_ready_record_not_submission"}
    context = browser.new_context(viewport={"width": width, "height": 960}, reduced_motion="reduce", accept_downloads=True, service_workers="block")
    context.add_init_script("localStorage.setItem('sb-pursuit-ui-test-auth-token', " + json.dumps(json.dumps(auth)) + ");")
    feed_requests = []
    document_review_reads, readiness_reads, eligibility_writes, review_writes = [], [], [], []
    def public_feed(request):
        assert request.method == "GET" and request.url.host == "boards-api.greenhouse.io"
        assert not {"authorization", "apikey", "cookie"}.intersection(request.headers)
        feed_requests.append(request.url.path)
        location = persona["job"]["location_text"]
        # Greenhouse's displayed location carries workplace information. Keep
        # this explicit instead of teaching tests to treat unknown as remote or
        # onsite. P11's UK NMC gap is unchanged; workplace is NOT eligibility.
        remote_only = persona["remote"] == "remote_only"
        workplace_prefix = {"remote_only": "Remote - ", "onsite": "Onsite - ", "hybrid": "Hybrid - "}.get(persona["remote"], "")
        jobs = [{"id": 123, "title": persona["job"]["title"],
            "content": persona["job"]["description"], "location": {"name": workplace_prefix + location},
            "updated_at": "2026-09-15T00:00:00Z"}]
        if remote_only:
            jobs.append({**jobs[0], "id": 124, "title": "Product Manager - Onsite", "location": {"name": "Onsite - " + location}, "content": "Product management on site five days a week. No remote work."})
        return feed_response({"jobs": jobs})
    app = create_app(settings=SETTINGS, transport=httpx.MockTransport(remote), studio=studio)
    original_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def fixture_lifespan(application):
        async with original_lifespan(application) as state:
            # Python 3.9 asyncio primitives must belong to the ASGI event loop.
            application.state.discovery = DiscoveryService(DiscoveryConfig((Board("greenhouse", "synthetic-careers"),)), transport=httpx.MockTransport(public_feed))
            yield state
    app.router.lifespan_context = fixture_lifespan
    with TestClient(app, raise_server_exceptions=False) as client, patch.dict(TOKENS, {auth["access_token"]: USER_A}), patch.object(studio, "_provider", return_value=provider), patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": persona["email"]}):
        def route_handler(route):
            request = route.request
            parsed = urlsplit(request.url)
            if parsed.hostname == "127.0.0.1" and parsed.port == 5187 and not parsed.path.startswith("/api/"):
                route.continue_(); return
            headers = {"access-control-allow-origin": "*", "access-control-allow-headers": "*"}
            if request.method == "OPTIONS":
                route.fulfill(status=204, headers=headers); return
            if parsed.hostname == "127.0.0.1" and parsed.port == 5187 and parsed.path.startswith("/api/mobile/"):
                response = client.request(request.method, parsed.path + ("?" + parsed.query if parsed.query else ""), content=request.post_data_buffer,
                                          headers={key: value for key, value in request.headers.items() if key in {"authorization", "content-type"}})
                if response.status_code >= 400:
                    result["errors"].append({"path": parsed.path, "status": response.status_code, "detail": response.json() if "json" in response.headers.get("content-type", "") else response.text[:200]})
                if request.method == "GET" and parsed.path.endswith("/document-reviews"):
                    document_review_reads.append(response.json())
                if request.method == "GET" and parsed.path.endswith("/readiness"):
                    readiness_reads.append(response.json())
                if request.method == "POST" and parsed.path.endswith("/eligibility"):
                    eligibility_writes.append(request.post_data_json)
                if request.method == "POST" and parsed.path.endswith("/review-packet"):
                    review_writes.append(request.post_data_json)
                route.fulfill(status=response.status_code, headers={**headers, "content-type": response.headers.get("content-type", "application/json")}, body=response.content); return
            if parsed.hostname == "pursuit-ui-test.supabase.co" and parsed.path.startswith(("/rest/", "/auth/")):
                response = remote(httpx.Request(request.method, "https://mobile.example.test" + parsed.path + ("?" + parsed.query if parsed.query else ""), content=request.post_data_buffer,
                                               headers={"apikey": SETTINGS.publishable_key, "authorization": "Bearer " + auth["access_token"]}))
                route.fulfill(status=response.status_code, headers=headers, body=response.content); return
            result["errors"].append({"blocked_external": parsed.hostname, "path": parsed.path})
            route.abort()
        context.route("**/*", route_handler)
        page = context.new_page()
        page.set_default_timeout(12000)
        page.on("pageerror", lambda error: result["errors"].append({"pageerror": str(error)}))
        try:
            page.goto(URL)
            page.get_by_role("textbox", name=re.compile("^Name")).fill(persona["name"])
            page.get_by_role("textbox", name=re.compile("Current city")).fill(persona["location"])
            career = page.get_by_role("textbox", name=re.compile("career facts", re.I))
            resume_only = persona["id"] == "P06"
            if not resume_only:
                career.fill("\n".join(persona["facts"]))
                page.get_by_role("checkbox", name=re.compile("reviewed these facts", re.I)).check()
            page.get_by_role("button", name="Continue", exact=True).click()
            page.get_by_role("textbox", name=re.compile("^Target roles")).fill(", ".join(persona["roles"]))
            page.get_by_role("textbox", name=re.compile("Preferred countries")).fill(", ".join(persona["locations"]))
            page.get_by_role("button", name="Continue", exact=True).click()
            page.get_by_role("combobox", name=re.compile("^Work preference")).select_option(persona["remote"])
            page.get_by_role("checkbox", name="I need visa sponsorship for relocation.", exact=True).set_checked(persona["sponsorship"])
            page.get_by_role("textbox", name=re.compile("Work authorisation")).fill(persona["constraints"])
            page.get_by_role("button", name="Continue", exact=True).click()
            page.locator("input[type=file]").set_input_files(str(ROOT / persona["resume"]))
            page.get_by_role("button", name="Continue", exact=True).click()
            page.get_by_role("button", name="Create my private workspace", exact=True).click()
            if resume_only:
                extracted = page.get_by_role("textbox", name="Review extracted career facts")
                expect(extracted).to_be_visible()
                assert extracted.input_value().strip()
                expect(page.get_by_text("Awaiting your review", exact=True)).to_be_visible()
                expect(page.get_by_text("Reviewed self-reported text", exact=True)).to_have_count(0)
                assert not provider.calls and not remote.tables["jobs"]
                assert len(remote.tables["resumes"]) == 1
                assert not remote.tables["profiles"][0].get("onboarding_completed_at")
                page.get_by_role("checkbox", name=re.compile("reviewed these extracted facts")).check()
                expect(page.get_by_text("Reviewed self-reported text", exact=True)).to_be_visible()
                page.get_by_role("button", name="Create my private workspace", exact=True).click()
                result["checks"].append("resume_only_extract_review_before_completion_no_duplicate_upload")
            expect(page.get_by_role("navigation", name="Primary navigation")).to_be_visible(timeout=60000)
            result["checks"].append("onboarding_with_real_upload_and_confirmed_facts")
            # Outcome regression: the UI discovers this posting itself. Never
            # seed /jobs behind the browser or fake the discovery API response.
            expect(page.get_by_role("heading", name="Roles to explore", exact=True)).to_be_visible(timeout=30000)
            if persona["remote"] == "remote_only":
                expect(page.get_by_role("heading", name="Product Manager - Onsite", exact=True)).to_have_count(0)
                result["checks"].append("onsite_decoy_not_recommended_to_remote_only_persona")
            assert len(feed_requests) == 1 and not remote.tables["jobs"] and not provider.calls
            card = page.get_by_role("article").filter(has=page.get_by_role("heading", name=persona["job"]["title"], exact=True)).first
            expect(card.get_by_role("heading", name="Why this role appeared")).to_be_visible()
            page.screenshot(path=str(OUT / f"{persona['id']}-recommendations.png"), full_page=True)
            result["checks"].append("automatic_discovery_from_confirmed_profile_without_seeded_jobs_or_AI")
            card.get_by_role("button", name="Save role", exact=True).click()
            expect(card.get_by_role("button", name="Open saved role", exact=True)).to_be_visible()
            assert len(remote.tables["jobs"]) == 1
            job_id = remote.tables["jobs"][0]["id"]
            card.get_by_role("button", name="Open saved role", exact=True).click()
            expect(page.get_by_role("region", name="Application Studio")).to_be_visible()
            if licence_gap:
                assert persona["remote"] == "onsite" and persona["job"]["location_text"] == "UK"
                assert "No UK NMC registration" in "\n".join(persona["facts"])
                assert "Current UK NMC registration mandatory" in persona["job"]["description"]
            status = "unknown" if licence_gap else "eligible"
            page.get_by_role("combobox", name=re.compile("^Your eligibility status")).select_option(status)
            page.get_by_label("Reason for this self-report").fill(
                "I do not hold UK NMC registration. The posting requires it. Sponsorship may be available, but does not establish registration or work rights. Eligibility remains unknown pending verification."
                if licence_gap else "Synthetic QA declaration only; not a real person or employer application.")
            page.get_by_role("checkbox", name=re.compile("explicitly confirm this job-specific")).check()
            page.get_by_role("button", name="Save eligibility self-report").click()
            expect(page.get_by_text("Your job-specific self-report was saved.", exact=False)).to_be_visible()
            assert [write["status"] for write in eligibility_writes] == [status]
            result["checks"].append("explicit_unknown_eligibility_missing_NMC_not_overridden" if licence_gap else "explicit_eligible_fixture_self_report_not_independent_verification")
            page.get_by_role("navigation", name="Application preparation steps").get_by_role("button", name="Documents", exact=True).click()
            page.get_by_role("checkbox", name=re.compile("Enable AI assistance")).check()
            page.get_by_role("button", name="Assess role with AI").click()
            expect(page.get_by_text("Role assessment saved.", exact=False)).to_be_visible()
            pending_questions = [q for q in remote.tables["mobile_questions"] if q["user_id"] == USER_A and q.get("status") != "answered"]
            if pending_questions:
                page.get_by_role("navigation", name="Application preparation steps").get_by_role("button", name=re.compile("^Questions")).click()
                for question in pending_questions:
                    form = page.get_by_role("form", name="Answer: " + question["prompt"], exact=True)
                    form.get_by_role("textbox").fill("Synthetic candidate self-report: " + persona["constraints"] + " Only the reviewed resume facts are confirmed; other qualifications are not established.")
                    form.get_by_role("button", name="Save answer", exact=True).click()
                    expect(page.get_by_text("Answer saved.", exact=False)).to_be_visible()
                result["checks"].append("outstanding_questions_answered_before_generation_without_automatic_AI")
                page.get_by_role("navigation", name="Application preparation steps").get_by_role("button", name="Documents", exact=True).click()
            page.get_by_role("button", name="Prepare draft documents").click()
            expect(page.get_by_text("Draft documents prepared.", exact=False)).to_be_visible()
            page.get_by_role("navigation", name="Application preparation steps").get_by_role("button", name="Documents", exact=True).click()
            expect(page.get_by_role("button", name="Download document")).to_have_count(4)
            # Real GET reads the persisted generation snapshot, not a replacement
            # mock payload or a freshly reconstructed current-profile narrative.
            evidence_panel = page.get_by_role("region", name="Document evidence review", exact=True)
            expect(evidence_panel.get_by_role("heading", name="What went into this draft?", exact=True)).to_be_visible()
            expect(evidence_panel.get_by_text(re.compile("^Generation-time snapshot, saved"))).to_be_visible()
            expect(evidence_panel.get_by_role("heading", name="Resume selections", exact=True)).to_be_visible()
            expect(evidence_panel.get_by_text(re.compile("^Cover-letter selections"))).to_be_visible()
            assert document_review_reads and document_review_reads[-1]["unavailable_count"] == 0
            review_rows = document_review_reads[-1]["reviews"]
            assert len(review_rows) == 1
            snapshot_review = review_rows[0]["review"]
            selected_ref = next(iter(snapshot_review["resume_sections"].values()))[0]
            selected_fact = next(source["text"] for source in snapshot_review["snapshot"]["sources"] if source["id"] == selected_ref)
            expect(evidence_panel.get_by_text(selected_fact, exact=True).first).to_be_visible()
            calls_before_review = list(provider.calls)
            review_reads_before = len(document_review_reads)
            with page.expect_response(lambda response: response.url.endswith(f"/jobs/{job_id}/document-reviews") and response.request.method == "GET"):
                evidence_panel.get_by_role("button", name="Refresh document reviews", exact=True).click()
            expect(evidence_panel.get_by_text(re.compile("^Generation-time snapshot, saved"))).to_be_visible()
            assert len(document_review_reads) == review_reads_before + 1
            assert document_review_reads[-1]["reviews"] == review_rows
            assert provider.calls == calls_before_review and not remote.tables["applications"]
            (OUT / f"{persona['id']}-document-review.json").write_text(json.dumps(document_review_reads[-1], indent=2) + "\n")
            evidence_panel.scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / f"{persona['id']}-generation-review.png"), full_page=True)
            result["checks"].append("visible_generation_time_evidence_and_explicit_refresh_same_snapshot_no_AI")
            with page.expect_download() as download:
                page.get_by_role("button", name="Download document").first.click()
            file = download.value
            file.save_as(str(OUT / (persona["id"] + "-download" + Path(file.suggested_filename).suffix)))
            assert not remote.tables["applications"], "Nothing may be submitted or recorded automatically"
            assert provider.calls == ["rank", "documents"], provider.calls
            assert len(remote.tables["artifacts"]) == 4
            for artifact in remote.tables["artifacts"]:
                artifact_name = Path(artifact["storage_path"]).name
                (OUT / (persona["id"] + "-" + artifact_name)).write_bytes(
                    remote.objects[("application-artifacts", artifact["storage_path"])]
                )
            page.get_by_role("navigation", name="Application preparation steps").get_by_role("button", name="Final check", exact=True).click()
            ready_button = page.get_by_role("button", name="Mark ready for manual apply")
            expect(ready_button).to_be_disabled()
            packet_select = page.get_by_role("combobox", name=re.compile("^Document packet"))
            packet_select.select_option(index=1)
            page.get_by_role("checkbox", name=re.compile("downloaded and reviewed the selected same-generation")).check()
            if licence_gap:
                expect(ready_button).to_be_disabled()
                expect(page.get_by_text("A current eligible self-report is required.", exact=True)).to_be_visible()
                assert remote.tables["jobs"][0]["eligibility_status"] == "unknown"
                assert not remote.tables["applications"] and not review_writes
                state = readiness_reads[-1]
                assert not state["ready"] and state["reason"] in {"eligibility_required", "pending_questions"}
                packet = next(p for p in state["packets"] if p["key"] == packet_select.input_value())
                assert packet["current"] and packet["issue"] is None
                # Isolated API negative probe: even a valid packet/fingerprint
                # cannot bypass the missing eligibility. Never sent to a site.
                rejection = client.post(f"/api/mobile/jobs/{job_id}/review-packet",
                    headers={"authorization": "Bearer " + auth["access_token"]},
                    json={"run_id": packet["run_id"], "resume_artifact_id": packet["artifacts"][0]["id"],
                        "letter_artifact_id": packet["artifacts"][1]["id"], "packet_fingerprint": packet["packet_fingerprint"], "confirmed": True})
                assert rejection.status_code == 409, rejection.text[:500]
                assert not remote.tables["applications"] and provider.calls == ["rank", "documents"]
                result["expected_rejections"].append({"probe": "real_ASGI_review_packet_with_valid_current_packet_but_unknown_eligibility", "status": rejection.status_code, "detail": rejection.json()})
                result["checks"].append("negative_NMC_gap_current_packet_review_cannot_mark_ready_UI_and_API_409")
            else:
                ready_button.click()
                expect(page.get_by_text("Your ready application record was saved.", exact=False)).to_be_visible()
                assert len(remote.tables["applications"]) == 1 and remote.tables["applications"][0]["status"] == "ready"
                assert not remote.tables["applications"][0].get("applied_at")
                assert len(review_writes) == 1
                result["checks"].append("same_generation_packet_explicit_ready_record_not_submission")
            result["outcome"] = result["expected_outcome"]
            result["checks"] += ["real_rank_pipeline_fixture_provider", "real_prepare_four_files_fixture_provider", "browser_download", "no_application_submission"]
            result["overflow"] = page.evaluate("document.documentElement.scrollWidth > innerWidth")
            assert not result["overflow"]
            assert not result["errors"], result["errors"]
            page.screenshot(path=str(OUT / f"{persona['id']}-{width}.png"), full_page=True)
            result["status"] = "passed"
        except Exception as error:
            result["status"] = "failed"
            result["failure"] = str(error)[:1600]
            page.screenshot(path=str(OUT / f"{persona['id']}-{width}-failure.png"), full_page=True)
        finally:
            result["provider_calls"] = provider.calls
            result["public_feed_requests"] = len(feed_requests)
            result["artifacts"] = len(remote.tables["artifacts"])
            result["document_review_reads"] = len(document_review_reads)
            result["browser_review_writes"] = len(review_writes)
            result["eligibility_status_writes"] = [write["status"] for write in eligibility_writes]
            result["final_readiness"] = readiness_reads[-1] if readiness_reads else None
            context.close()
    return result


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-personas", action="store_true", help="Run the full fourteen-persona repaired core journey")
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    OUT = args.output_dir.resolve()
    if not OUT.is_relative_to(ROOT / "docs/testing/evidence"):
        parser.error("Evidence must stay under docs/testing/evidence")
    OUT.mkdir(parents=True, exist_ok=True)
    personas = json.loads((ROOT / "tests/fixtures/resumes/personas.json").read_text())
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        cases = [(p["id"], (1440, 390, 768)[index % 3]) for index, p in enumerate(personas)] if args.all_personas else [("P01", 1440), ("P03", 390), ("P06", 768)]
        for identifier, width in cases:
            result = run_persona(browser, next(p for p in personas if p["id"] == identifier), width)
            results.append(result)
            (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(identifier, result["status"], result.get("outcome", result.get("failure", "")), flush=True)
        browser.close()
    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"total": len(results), "passed": sum(row["status"] == "passed" for row in results),
        "failed": sum(row["status"] != "passed" for row in results),
        "expected_negative_passes": sum(row["status"] == "passed" and bool(row["expected_rejections"]) for row in results),
        "boundary": "real React/ASGI/repository/studio; synthetic public feed/Auth/DB/Storage/provider"}), flush=True)
    return 0 if all(row["status"] == "passed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
