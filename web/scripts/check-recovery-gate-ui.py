"""Actual React recovery/spend-boundary checks with intercepted synthetic APIs.

No real provider, auth, storage, deletion or spend. The shared Playwright harness
blocks non-loopback traffic. Missing-byte responses mirror the backend contract.
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
OLD = "00000000-0000-4000-8000-000000000031"
NEW = "00000000-0000-4000-8000-000000000032"
SOURCE = "00000000-0000-4000-8000-000000000033"
OTHER_JOB = "00000000-0000-4000-8000-000000000034"
OP = {"id": OLD, "job_id": fixture.JID, "state": "upload_pending", "filename": "unrecoverable-resume.pdf"}


def recovery_context(browser, width=390, mode="missing", pending=True):
    context, page, base = fixture.context_for(browser, width)
    state = {"pending": [copy.deepcopy(OP)] if pending else [], "mode": mode, "gets": 0, "recovers": [], "prepares": [], "uploads": 0, "held": [], "job": copy.deepcopy(fixture.JOB)}

    def intercept(route):
        req = route.request
        path = urlsplit(req.url).path
        assert req.headers.get("authorization", "").startswith("Bearer ")
        if path == "/api/mobile/artifact-operations":
            assert req.method == "GET"
            state["gets"] += 1
            if state["mode"] == "hold":
                state["held"].append(route)
            elif state["mode"] == "list_failure":
                route.fulfill(status=503, json={"detail": "Synthetic recovery list unavailable."})
            elif state["mode"] == "malformed_list":
                route.fulfill(json={"unexpected": []})
            else:
                route.fulfill(json=state["pending"])
        elif path.startswith("/api/mobile/artifact-operations/") and path.endswith("/recover"):
            assert req.method == "POST"
            operation_id = path.split("/")[-2]
            assert any(row["id"] == operation_id for row in state["pending"])
            state["recovers"].append(operation_id)
            code = "artifact_upload_bytes_required" if state["mode"] != "generic_failure" else "artifact_recovery_required"
            proof_id = NEW if state["mode"] == "wrong_id" else operation_id
            status = 404 if state["mode"] == "not_found" else 503 if state["mode"] == "generic_failure" else 409
            route.fulfill(status=status, json={"detail": {"code": code, "operation_id": proof_id, "message": "Synthetic artifact bytes are missing; old journal retained."}})
        elif path == "/api/mobile/resumes" and req.method == "POST":
            assert req.headers.get("idempotency-key")
            state["uploads"] += 1
            resume = {**fixture.RESUME, "id": SOURCE, "label": "Fresh synthetic source", "original_filename": req.post_data_json["filename"], "is_default": False}
            base["data"]["resumes"].append(resume)
            route.fulfill(status=201, json=resume)
        elif path == f"/api/mobile/jobs/{fixture.JID}/prepare":
            assert req.method == "POST"
            state["prepares"].append(req.post_data_json)
            artifact = {**fixture.ARTIFACT, "resume_id": SOURCE}
            base["data"]["artifacts"].append(artifact)
            route.fulfill(json={"artifacts": [artifact], "questions": []})
        elif path == f"/api/mobile/jobs/{OTHER_JOB}":
            assert req.method == "GET"
            route.fulfill(json=state["job"])
        else:
            raise AssertionError((req.method, path))

    context.route(re.compile(r"^http://127\.0\.0\.1:5187/api/mobile/(?:artifact-operations(?:/[a-z0-9-]+/recover)?|resumes|jobs/" + fixture.JID + r"/prepare|jobs/" + OTHER_JOB + r")$"), intercept)
    return context, page, state, base


def docs(page):
    fixture.open_studio(page)
    page.get_by_role("button", name="Documents", exact=True).click()
    page.get_by_role("checkbox", name="Enable AI assistance", exact=False).check()


def recover(page, index=0):
    page.get_by_role("button", name="Recover saved artifact", exact=True).nth(index).click()


def fresh_and_ack(page):
    page.get_by_label("Source resume file", exact=True).set_input_files({"name": "fresh-source.pdf", "mimeType": "application/pdf", "buffer": b"synthetic fixture bytes; API validation is intercepted"})
    expect(page.get_by_role("combobox", name="Source resume", exact=True).locator(f'option[value="{SOURCE}"]')).to_have_count(1)
    expect(page.get_by_role("checkbox", name="I acknowledge missing file", exact=False)).to_be_disabled()
    page.get_by_role("combobox", name="Source resume", exact=True).select_option(SOURCE)
    expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
    page.get_by_role("checkbox", name="I acknowledge missing file", exact=False).check()
    expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="Assess role with AI", exact=True)).to_be_disabled()


def clean(state, base, prepares=0):
    assert len(state["prepares"]) == prepares
    assert base["rank_calls"] == 0 and base["prepare_calls"] == 0
    assert not base["writes"] and not base["unexpected"], base["unexpected"]
    assert any(row["id"] == OLD for row in state["pending"]) or not state["recovers"]


def main():
    output = Path(__file__).resolve().parents[1] / "node_modules/.cache/recovery-gate-ui"
    output.mkdir(parents=True, exist_ok=True)
    evidence = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width in [1440, 390]:
            context, page, state, base = recovery_context(browser, width)
            docs(page)
            expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
            expect(page.get_by_role("checkbox", name="I acknowledge missing file", exact=False)).to_have_count(0)
            recover(page)
            expect(page.get_by_text("Recovery confirmed missing artifact bytes.", exact=False)).to_be_visible()
            fresh_and_ack(page)
            assert len(state["recovers"]) == 1 and not state["prepares"] and state["uploads"] == 1
            page.get_by_role("checkbox", name="I acknowledge missing file", exact=False).evaluate("el => el.scrollIntoView({block: 'center'})")
            fixture.no_overflow(page)
            page.screenshot(path=str(output / f"recovery-{width}-explicit-ack.png"))
            page.get_by_role("button", name="Prepare draft documents", exact=True).click()
            expect(page.get_by_text("Draft documents prepared.", exact=False)).to_be_visible()
            expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
            assert state["prepares"] == [{"resume_id": SOURCE, "variant": "role_aligned"}]
            assert state["pending"] == [OP], "Old journal must not be resolved, cancelled, or deleted by new preparation"
            clean(state, base, prepares=1)
            # Leaving/reopening Studio must not retain proof or acknowledgement.
            page.get_by_role("button", name="Close", exact=True).click()
            page.get_by_role("button", name="Prepare", exact=True).click()
            page.get_by_role("button", name="Documents", exact=True).click()
            page.get_by_role("checkbox", name="Enable AI assistance", exact=False).check()
            expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
            expect(page.get_by_role("checkbox", name="I acknowledge missing file", exact=False)).to_have_count(0)
            state["job"] = {**fixture.JOB, "id": OTHER_JOB, "title": "Another synthetic role"}
            base["data"]["jobs"].append(state["job"])
            state["pending"].append({**OP, "id": NEW, "job_id": OTHER_JOB, "filename": "other-role-document.pdf"})
            page.get_by_role("button", name="Close", exact=True).click()
            page.locator(".beta-job").filter(has_text="Another synthetic role").get_by_role("button", name="Prepare", exact=True).click()
            page.get_by_role("button", name="Documents", exact=True).click()
            page.get_by_role("checkbox", name="Enable AI assistance", exact=False).check()
            expect(page.get_by_role("region", name="artifact recovery")).to_contain_text("other-role-document.pdf")
            expect(page.get_by_role("region", name="artifact recovery")).not_to_contain_text("unrecoverable-resume.pdf")
            expect(page.get_by_role("checkbox", name="I acknowledge missing file", exact=False)).to_have_count(0)
            expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
            clean(state, base, prepares=1)
            evidence.append(f"PASS {width}px missing bytes -> fresh source -> explicit per-operation acknowledgement -> one Prepare; journal retained; reopening/job change clears consent and old rows; recovery AI=0")
            print(evidence[-1]); context.close()

        context, page, state, base = recovery_context(browser, mode="hold", pending=False)
        docs(page)
        expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Assess role with AI", exact=True)).to_be_disabled()
        assert state["held"]
        state["mode"] = "missing"
        for route in state["held"]: route.fulfill(json=[])
        expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_enabled()
        clean(state, base)
        evidence.append("PASS initial pending-record load blocks AI until verified empty")
        print(evidence[-1]); context.close()

        for mode in ["list_failure", "malformed_list"]:
            context, page, state, base = recovery_context(browser, mode=mode)
            docs(page)
            expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
            expect(page.get_by_role("region", name="artifact recovery")).to_contain_text("Recovery status is unknown")
            state["mode"] = "missing"
            page.get_by_role("button", name="Check pending artifact operations", exact=True).click()
            expect(page.get_by_role("button", name="Recover saved artifact", exact=True)).to_be_visible()
            state["mode"] = "list_failure"
            page.get_by_role("button", name="Check pending artifact operations", exact=True).click()
            expect(page.get_by_role("region", name="artifact recovery")).to_contain_text("Recovery status is unknown")
            expect(page.get_by_text(OP["filename"], exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
            page.get_by_role("region", name="artifact recovery").evaluate("el => el.scrollIntoView({block: 'center'})")
            page.screenshot(path=str(output / f"recovery-390-{mode}.png"))
            clean(state, base)
            evidence.append(f"PASS {mode} blocks AI, preserves known recovery rows, never converts failure into empty")
            print(evidence[-1]); context.close()

        for mode in ["wrong_id", "not_found", "generic_failure"]:
            context, page, state, base = recovery_context(browser, mode=mode)
            docs(page); recover(page)
            expect(page.get_by_role("region", name="artifact recovery")).to_contain_text("Recovery status is unknown")
            expect(page.get_by_role("checkbox", name="I acknowledge missing file", exact=False)).to_have_count(0)
            expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
            clean(state, base)
            evidence.append(f"PASS {mode} is not accepted as proof of missing bytes; no recovery AI")
            print(evidence[-1]); context.close()

        context, page, state, base = recovery_context(browser)
        docs(page); recover(page); fresh_and_ack(page)
        state["pending"].append({**OP, "id": NEW, "filename": "second-unresolved.pdf"})
        page.get_by_role("button", name="Prepare draft documents", exact=True).click()
        expect(page.get_by_role("alert").filter(has_text="Pending recovery changed")).to_be_visible()
        expect(page.get_by_role("button", name="Prepare draft documents", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Recover saved artifact", exact=True)).to_have_count(2)
        expect(page.get_by_role("checkbox", name="I acknowledge missing file", exact=False)).to_have_count(0)
        page.get_by_role("alert").filter(has_text="Pending recovery changed").evaluate("el => el.scrollIntoView({block: 'center'})")
        page.screenshot(path=str(output / "recovery-390-new-operation-blocks.png"))
        clean(state, base)
        evidence.append("PASS new pending operation discovered at pre-spend check invalidates old acknowledgement; Prepare/AI calls=0")
        print(evidence[-1]); context.close()
        browser.close()
    (output / "results.json").write_text(json.dumps({"fixture_only": True, "passed": len(evidence), "scenarios": evidence, "live_services": False}, indent=2) + "\n")
    print(f"{len(evidence)} isolated recovery-gate browser scenarios passed; no actual providers or paid AI.")


if __name__ == "__main__":
    main()
