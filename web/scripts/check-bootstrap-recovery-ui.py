"""Real Chromium + React, synthetic HTTP services only; never point at production.

Rebuild with build-workflow-fixture.mjs; reuse its loopback preview on 5187.
Extends check-workflow-ui.py fixtures without editing product or production output.
This checks UI consumption, not ASGI/SQL/RLS or real model/provider behavior.
"""
import copy
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

SCRIPT = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("workflow_fixture", SCRIPT.with_name("check-workflow-ui.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
OUT = SCRIPT.parents[2] / "docs/testing/evidence/core-launch-20260915/readiness-recovery-browser"


def seed_reviewed_and_unavailable(state):
    packet = fixture.seed_server_packet(state)
    ready = state["readiness"][fixture.JID]
    ready.update(ready=True, reason=None, application_status="ready", recorded_status="ready", application_id=fixture.APP_ID,
        review={"id": fixture.REVIEW_ID, "run_id": packet["run_id"], "resume_artifact_id": fixture.AID,
                "letter_artifact_id": fixture.LETTER_ID, "packet_fingerprint": packet["packet_fingerprint"],
                "current": True, "reviewed_at": fixture.STAMP})
    app = {"id": fixture.APP_ID, "user_id": fixture.UID, "job_id": fixture.JID, "status": "ready",
           "recorded_status": "ready", "applied_at": None, "updated_at": fixture.STAMP, "readiness": copy.deepcopy(ready)}
    job = state["data"]["jobs"][0]
    job.update(status="ready", application_status="ready", eligibility_status="eligible")
    recovered = {"job": copy.deepcopy(job), "app": copy.deepcopy(app), "readiness": copy.deepcopy(ready)}

    # A distinct fully specified server fixture supplies the unaffected Ready
    # positive control. IDs/pair ownership remain consistent across its objects.
    mapping = {value: f"00000000-0000-4000-8000-{index:012d}" for index, value in enumerate(
        [fixture.JID, fixture.AID, fixture.LETTER_ID, fixture.RUN_ID, fixture.APP_ID, fixture.REVIEW_ID], 20)}
    def remap(value):
        if isinstance(value, dict): return {key: remap(item) for key, item in value.items()}
        if isinstance(value, list): return [remap(item) for item in value]
        if isinstance(value, str):
            if value == fixture.RUN_ID + ":pdf": return mapping[fixture.RUN_ID] + ":pdf"
            return mapping.get(value, value)
        return value
    healthy_job, healthy_app, healthy_readiness = remap(job), remap(app), remap(ready)
    healthy_job["title"] = "Unaffected Reviewed Designer"
    healthy_job["source_url"] = "https://example.invalid/careers/healthy-designer"
    state["data"]["jobs"].append(healthy_job)
    state["data"]["artifacts"].extend(remap(copy.deepcopy(state["data"]["artifacts"])))
    state["readiness"][healthy_job["id"]] = healthy_readiness
    job.update(status="matched", application_status="draft", readiness_unavailable="packet_check_failed")
    unavailable = {**app, "status": "draft", "readiness_unavailable": "packet_check_failed"}
    unavailable.pop("readiness")
    state["data"]["applications"] = [unavailable, healthy_app]
    return recovered


def run_case(browser, width, recovery):
    context, page, state = fixture.context_for(browser, width)
    recovered = seed_reviewed_and_unavailable(state)
    events, snapshots, pending, screenshots = [], [], [], []
    bootstrap_reads = []
    control = {"hold": False}
    phase = f"{width}-{recovery}"

    def record(request):
        url = urlsplit(request.url)
        if url.path.startswith("/api/"):
            events.append({"method": request.method, "path": url.path})
    page.on("request", record)

    def bootstrap(route):
        assert route.request.method == "GET" and not route.request.post_data
        bootstrap_reads.append(len(events))
        if control["hold"]:
            pending.append(route)
        else:
            route.fallback()  # original fixture verifies Auth and returns data
    page.route(re.compile(r"^http://127\.0\.0\.1:5187/api/mobile/bootstrap$"), bootstrap)

    def screenshot(suffix):
        fixture.no_overflow(page)
        name = phase + "-" + suffix + ".png"
        page.screenshot(path=str(OUT / name), full_page=True)
        screenshots.append(name)

    def tracker_counts(stage, expected_ready):
        page.get_by_role("navigation", name="Primary navigation").get_by_role("button", name="Tracker", exact=True).click()
        rows = page.locator(".beta-applications-workspace-row")
        expect(rows).to_have_count(2)
        expect(rows.locator(".beta-applications-workspace-status.ready")).to_have_count(expected_ready)
        expect(rows.locator(".beta-applications-workspace-status.draft")).to_have_count(2 - expected_ready)
        expect(rows.filter(has=page.get_by_role("heading", name="Unaffected Reviewed Designer", exact=True))).to_contain_text("Ready")
        snapshots.append({"stage": stage, "ready": rows.locator(".beta-applications-workspace-status.ready").count(),
                          "nonready": rows.locator(".beta-applications-workspace-status.draft").count(),
                          "bootstrap_gets": len(bootstrap_reads)})

    try:
        page.goto(fixture.BASE)
        expect(page.get_by_role("heading", name="Your next move, Alex.")).to_be_visible()
        warning = page.get_by_role("alert").filter(has_text="Readiness could not be verified for 1 saved role.")
        expect(warning).to_be_visible()
        expect(warning).to_contain_text("These roles are not marked Ready.")
        expect(warning).to_contain_text("this check does not run AI or submit applications")
        page.get_by_role("button", name="Your profile", exact=True).click()
        expect(page.get_by_role("heading", name="Your story, on your terms.")).to_be_visible()
        expect(page.get_by_text(fixture.PROFILE["career_text"], exact=True)).to_be_visible()
        tracker_counts("unavailable", 1)
        assert len(bootstrap_reads) == 1, "Navigation must not automatically retry readiness"
        screenshot("warning")

        page.reload()
        expect(warning).to_be_visible()
        tracker_counts("unavailable-after-reload", 1)
        assert len(bootstrap_reads) == 2
        control["hold"] = True
        before_retry = len(events)
        page.get_by_role("button", name="Retry readiness checks", exact=True).click()
        expect(page.get_by_role("button", name="Checking readiness…", exact=True)).to_be_disabled()
        assert len(pending) == 1 and len(bootstrap_reads) == 3
        assert events[before_retry:] == [{"method": "GET", "path": "/api/mobile/bootstrap"}], "One click must issue only one bootstrap GET"

        if recovery == "unknown":
            recovered["job"].update(status="matched", application_status="draft", eligibility_status="unknown",
                eligibility_review={"status": "unknown", "confirmed": True, "reason": "Synthetic qualification not confirmed."})
            recovered["readiness"].update(ready=False, reason="eligibility_required", application_status="draft")
            recovered["readiness"]["review"]["current"] = False
            recovered["app"].update(status="draft", readiness=copy.deepcopy(recovered["readiness"]))
        state["data"]["jobs"][0] = recovered["job"]
        state["data"]["applications"][0] = recovered["app"]
        state["readiness"][fixture.JID] = recovered["readiness"]
        control["hold"] = False
        pending.pop().fallback()
        expect(warning).to_have_count(0)
        expect(page.get_by_role("button", name="Retry readiness checks", exact=True)).to_have_count(0)
        expected_ready = 2 if recovery == "valid" else 1
        tracker_counts("recovered", expected_ready)
        screenshot("recovered")
        page.reload()
        expect(page.get_by_role("heading", name="Your next move, Alex.")).to_be_visible()
        expect(warning).to_have_count(0)
        tracker_counts("recovered-after-reload", expected_ready)
        assert len(bootstrap_reads) == 4
        screenshot("reloaded")

        if recovery == "unknown":
            row = page.locator(".beta-applications-workspace-row").filter(has=page.get_by_role("heading", name=fixture.JOB["title"], exact=True))
            row.get_by_role("button", name="Open studio", exact=True).click()
            expect(page.get_by_role("textbox", name="Full job description", exact=True)).to_have_value(fixture.JOB["description"])
            ready = fixture.final_review(page, recovered["readiness"]["packets"][0])
            # Download and acknowledge the actual fixture pair. Even full local
            # document review must not turn unknown eligibility into Ready.
            for label in ("resume", "cover letter"):
                with page.expect_download():
                    page.get_by_role("button", name="Download selected " + label, exact=True).click()
            fixture.review_checkbox(page).check()
            expect(ready).to_be_disabled()
            expect(page.get_by_text("A current eligible self-report is required.", exact=True)).to_be_visible()
            screenshot("unknown-still-blocked")

        assert len(bootstrap_reads) == (5 if recovery == "unknown" else 4)
        fixture.review_counts(state, 0)
        assert state["uploads"] == 0
        assert all(event["method"] == "GET" for event in events), "No AI or mutation endpoint may be called"
        assert all(row["applied_at"] is None for row in state["data"]["applications"])
        assert not state["unexpected"], state["unexpected"]
        return {"width": width, "recovery": recovery, "status": "passed", "snapshots": snapshots,
                "bootstrap_gets": len(bootstrap_reads), "explicit_retries": 1,
                "api_gets": dict(Counter(event["path"] for event in events)), "writes": len(state["writes"]),
                "ai_calls": state["rank_calls"] + state["prepare_calls"], "packet_review_writes": len(state["review_calls"]),
                "unexpected": state["unexpected"], "screenshots": screenshots}
    except Exception as error:
        screenshot("failure")
        return {"width": width, "recovery": recovery, "status": "failed", "error": str(error),
                "snapshots": snapshots, "events": events, "unexpected": state["unexpected"], "screenshots": screenshots}
    finally:
        context.close()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width in (390, 1440):
            for recovery in ("valid", "unknown"):
                row = run_case(browser, width, recovery)
                results.append(row)
                print(json.dumps(row), flush=True)
        browser.close()
    report = {"fixture_only": True, "live_services": False, "browser": "Chromium",
              "boundary": "Real React/browser; synthetic Auth/HTTP data, not a real backend or SQL/RLS test.",
              "passed": sum(row["status"] == "passed" for row in results), "total": len(results), "scenarios": results}
    (OUT / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
