"""Actual React/Chromium discovery journeys with intercepted API fixtures only.

Reuses the repository web workflow harness. No live feeds, auth, providers,
payments, founder APIs, emails or saved real-user data are contacted.
"""
import copy
import hashlib
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
STAMP = "2026-09-15T00:00:00Z"
SAVED_ID = "00000000-0000-4000-8000-000000000020"


def discovery_context(browser, width=390, scheme="light", *, profession="Registered Nurse", mode="partial", duplicate=False):
    context, page, base = fixture.context_for(browser, width, scheme)
    base["data"]["jobs"] = []
    base["data"]["preferences"].update(target_titles=[profession], preferred_locations=["Toronto"], remote_preference="open")
    job = {
        "source_id": "ats_" + hashlib.sha256(profession.encode()).hexdigest(), "source": "greenhouse:fixture-board", "provider": "greenhouse", "board": "fixture-board", "external_id": "123",
        "title": profession, "company_name": "fixture-board", "company_name_is_board_identifier": True,
        "source_url": "https://job-boards.greenhouse.io/fixture-board/jobs/123", "description": "Synthetic posting: review registration and work-rights requirements.\n<script>window.discoveryInjection = true</script>",
        "location_text": "Toronto", "country": "Canada", "department": "Practice", "employment_type": "Full time", "workplace_type": "onsite",
        "content_truncated": True, "fetched_at": STAMP, "source_updated_at": STAMP, "source_published_at": None, "source_created_at": None,
        "match_reasons": ["Matches stored target title.", "Matches stored location/region."], "eligibility_status": "unknown", "persisted": False,
        "eligibility": {"status": "unknown", "provisional": True, "independently_verified": False, "review_required": True, "reasons": ["Work authorisation and qualifications need review.", "Posting text is truncated; check the original."]},
    }
    source = {"source": job["source"], "status": "partial", "cached": True, "fetched_at": STAMP, "checked_at": STAMP, "received_count": 3, "returned_count": 1, "dropped_count": 1, "unlisted_count": 0, "duplicate_count": 1, "truncated": True, "error_code": None, "retry_after": 0}
    failed = {**source, "source": "lever:second-fixture-board", "status": "error", "fetched_at": None, "cached": False, "received_count": 0, "returned_count": 0, "dropped_count": 0, "duplicate_count": 0, "truncated": False, "error_code": "timeout", "retry_after": 60}
    response = {"status": "partial", "results": [job], "sources": [source, failed], "partial": True, "truncated": True, "matched_count": 1, "returned_count": 1, "searched_at": STAMP, "persisted": False, "eligibility_verified": False}
    state = {"mode": mode, "searches": [], "saves": [], "detail_ids": [], "duplicate": duplicate, "response": response}

    def route_discovery(route):
        request = route.request
        path = urlsplit(request.url).path
        assert request.headers.get("authorization", "").startswith("Bearer ") and "apikey" not in request.headers
        if path == "/api/mobile/discovery/search":
            assert request.method == "POST"
            body = request.post_data_json
            assert set(body) == {"query", "filters", "limit"} and set(body["filters"]) == {"titles", "locations", "workplace_type"} and body["limit"] == 20
            state["searches"].append(body)
            if state["mode"] in ("not_configured", "rate_limited"):
                route.fulfill(status=429 if state["mode"] == "rate_limited" else 503, json={"detail": {"code": state["mode"], "message": "Discovery search limit reached. Retry later." if state["mode"] == "rate_limited" else "No public discovery boards are configured."}}, headers={"Retry-After": "60"})
                return
            result = copy.deepcopy(response)
            if state["mode"] in ("unavailable", "unavailable_200"):
                result.update(status="unavailable", results=[], matched_count=0, returned_count=0, truncated=False, sources=[failed])
            elif state["mode"] == "empty":
                result.update(status="ok", results=[], matched_count=0, returned_count=0, partial=False, truncated=False, sources=[{**source, "status": "ok", "truncated": False, "dropped_count": 0}])
                result["warnings"] = ["Some saved regions have no supported country map; only literal location text can match them.", "Some postings have unconfirmed geography. Remote results need review."]
            elif state["mode"] == "unsafe":
                result["results"][0]["source_url"] = "javascript:alert(1)"
            route.fulfill(status=503 if state["mode"] == "unavailable" else 200, json=result)
        elif path == "/api/mobile/jobs":
            assert request.method == "POST"
            body = request.post_data_json
            assert set(body) == {"source_url", "title", "company_name", "description", "location_text"}
            assert body["description"] == job["description"]
            state["saves"].append(body)
            if state["mode"] == "save_failure":
                route.fulfill(status=503, json={"detail": "Synthetic save interruption. Refresh saved roles before retrying."})
                return
            saved = {**fixture.JOB, **body, "id": SAVED_ID, "eligibility_status": "unknown", "status": "new", "duplicate": state["duplicate"]}
            base["data"]["jobs"] = [saved]
            route.fulfill(status=200 if state["duplicate"] else 201, json=saved)
        elif path == f"/api/mobile/jobs/{SAVED_ID}":
            assert request.method == "GET"
            state["detail_ids"].append(SAVED_ID)
            route.fulfill(json=base["data"]["jobs"][0])
        else:
            raise AssertionError((request.method, path))

    context.route(re.compile(r"^http://127\.0\.0\.1:5187/api/mobile/(?:discovery/search|jobs|jobs/" + SAVED_ID + r")$"), route_discovery)
    return context, page, state, base


def open_discovery(page):
    page.goto(fixture.BASE)
    page.get_by_role("navigation", name="Primary navigation").get_by_role("button", name="Discover", exact=True).click()
    page.get_by_role("button", name="Search public boards", exact=True).click()
    expect(page.get_by_role("heading", name="Find roles on public boards.", exact=True)).to_be_visible()


def clean(state, base):
    assert not base["unexpected"], base["unexpected"]
    assert not base["writes"], "No unrelated workspace writes or AI calls permitted"
    assert base["rank_calls"] == 0 and base["prepare_calls"] == 0


def main():
    output = Path(__file__).resolve().parents[1] / "node_modules/.cache/discovery-ui"
    output.mkdir(parents=True, exist_ok=True)
    evidence = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width, scheme, profession in [(1440, "light", "Registered Nurse"), (390, "light", "Teacher"), (320, "dark", "Manufacturing Technician")]:
            context, page, state, base = discovery_context(browser, width, scheme, profession=profession)
            open_discovery(page)
            assert not state["searches"] and not state["saves"], "Opening discovery must not search or save automatically"
            skip = page.locator(".pursuit-skip")
            assert skip.evaluate("el => getComputedStyle(el).clipPath") == "inset(50%)"
            skip.focus()
            assert skip.evaluate("el => getComputedStyle(el).clipPath") == "none", "Keyboard skip link must remain available"
            page.keyboard.press("Tab")
            page.get_by_text("Saved preferences applied automatically", exact=True).click()
            expect(page.locator(".discovery-preferences")).to_contain_text(profession)
            page.get_by_role("button", name="Search public boards now", exact=True).click()
            expect(page.get_by_role("heading", name="Public board results — not saved", exact=True)).to_be_visible()
            assert state["searches"] == [{"query": "", "filters": {"titles": [], "locations": [], "workplace_type": "any"}, "limit": 20}]
            assert not state["saves"]
            expect(page.get_by_text("Partial coverage:", exact=False)).to_be_visible()
            expect(page.get_by_text("Eligibility unknown · review required", exact=True)).to_be_visible()
            page.get_by_text("Review posting and provisional reasons", exact=True).click()
            expect(page.locator(".discovery-description")).to_contain_text("<script>")
            assert page.evaluate("window.discoveryInjection") is None
            fixture.no_overflow(page)
            page.screenshot(path=str(output / f"discovery-{width}-{scheme}-partial.png"), full_page=True)
            page.get_by_role("button", name="Save role", exact=True).click()
            expect(page.get_by_role("button", name="Open saved role", exact=True)).to_be_visible()
            assert len(state["saves"]) == 1 and base["data"]["jobs"][0]["id"] == SAVED_ID
            page.screenshot(path=str(output / f"discovery-{width}-{scheme}-saved.png"), full_page=True)
            page.get_by_role("button", name="Open saved role", exact=True).click()
            expect(page.get_by_role("textbox", name="Full job description", exact=True)).to_have_value(state["saves"][0]["description"])
            assert state["detail_ids"] == [SAVED_ID], "Studio must use saved UUID, never public source_id"
            page.screenshot(path=str(output / f"discovery-{width}-{scheme}-studio.png"))
            clean(state, base)
            evidence.append(f"PASS real React fixture discovery/explicit save/Studio {width}px {scheme}; profession={profession}; searches=1 saves=1 AI=0")
            print(evidence[-1])
            context.close()

        for width, mode in [(390, "unavailable"), (1440, "unavailable_200"), (390, "empty"), (1440, "not_configured"), (390, "rate_limited"), (390, "unsafe")]:
            context, page, state, base = discovery_context(browser, width, mode=mode)
            open_discovery(page)
            page.get_by_role("button", name="Search public boards now", exact=True).click()
            if mode.startswith("unavailable"):
                expect(page.get_by_role("alert")).to_contain_text("All configured discovery sources failed")
                expect(page.locator(".discovery-sources")).to_contain_text("timeout")
            elif mode == "empty":
                expect(page.get_by_role("heading", name="No matching roles in the fetched sample", exact=True)).to_be_visible()
                expect(page.get_by_role("region", name="Discovery coverage and geography warnings")).to_contain_text("Some saved regions have no supported country map")
                expect(page.get_by_role("region", name="Discovery coverage and geography warnings")).to_contain_text("Some postings have unconfirmed geography")
            elif mode == "not_configured":
                expect(page.get_by_role("alert")).to_contain_text("No public discovery boards are configured")
            elif mode == "rate_limited":
                expect(page.get_by_role("alert")).to_contain_text("Discovery search limit reached")
                expect(page.get_by_text("The service asked you to wait 60 seconds", exact=False)).to_be_visible()
            else:
                expect(page.get_by_role("alert")).to_contain_text("unexpected result")
            expect(page.get_by_role("button", name="Save role", exact=True)).to_have_count(0)
            assert not state["saves"] and len(state["searches"]) == 1
            fixture.no_overflow(page)
            page.screenshot(path=str(output / f"discovery-{width}-{mode}.png"), full_page=True)
            if mode == "empty":
                page.get_by_role("region", name="Discovery coverage and geography warnings").evaluate("el => el.scrollIntoView({block: 'center'})")
                page.screenshot(path=str(output / f"discovery-{width}-zero-results-warnings.png"))
            if mode != "empty":
                page.get_by_role("alert").evaluate("el => el.scrollIntoView({block: 'center'})")
                page.screenshot(path=str(output / f"discovery-{width}-{mode}-error-viewport.png"))
            clean(state, base)
            evidence.append(f"PASS real React fixture {mode} {width}px; searches=1 saves=0 AI=0")
            print(evidence[-1])
            context.close()

        for mode in ["duplicate", "save_failure"]:
            context, page, state, base = discovery_context(browser, mode="partial" if mode == "duplicate" else mode, duplicate=mode == "duplicate")
            open_discovery(page)
            page.get_by_role("button", name="Search public boards now", exact=True).click()
            page.get_by_role("button", name="Save role", exact=True).click()
            if mode == "duplicate":
                expect(page.get_by_text("This posting was already saved.", exact=False)).to_be_visible()
                expect(page.get_by_role("button", name="Open saved role", exact=True)).to_be_visible()
            else:
                expect(page.get_by_role("alert")).to_contain_text("Synthetic save interruption")
                expect(page.get_by_role("button", name="Save role", exact=True)).to_be_disabled()
            assert len(state["saves"]) == 1
            page.screenshot(path=str(output / f"discovery-390-{mode}.png"), full_page=True)
            clean(state, base)
            evidence.append(f"PASS real React fixture {mode} 390px; one explicit save attempt, no automatic retry")
            print(evidence[-1])
            context.close()
        browser.close()
    # Generated test output, not a master report. Only synthetic route counts and UI results.
    (output / "results.json").write_text(json.dumps({"fixture_only": True, "scenarios": evidence, "passed": len(evidence), "live_services": False}, indent=2) + "\n")
    print("11 isolated discovery browser scenarios passed. All API responses are fixtures; no live feed/provider requests.")


if __name__ == "__main__":
    main()
