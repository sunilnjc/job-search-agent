"""Repair acceptance: actual React UI -> real ASGI/repository/studio -> fake cloud.

Requires the explicitly synthetic Vite instance on 127.0.0.1:5178. No emails,
external job requests or paid provider calls. Historical audit evidence is kept.
"""
import base64
import json
import os
import re
import sys
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
from test_mobile_api import FakeSupabase, SETTINGS, TOKENS, USER_A
from web_ui_smoke import session

OUT = ROOT / "docs/testing/evidence/repairs-20260915/browser"
URL = "http://127.0.0.1:5178/beta"


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
    result = {"persona": persona["id"], "width": width, "boundary": "real React/ASGI/studio; fake Auth/DB/Storage/provider", "errors": [], "checks": []}
    context = browser.new_context(viewport={"width": width, "height": 960}, reduced_motion="reduce", accept_downloads=True)
    context.add_init_script("localStorage.setItem('sb-pursuit-ui-test-auth-token', " + json.dumps(json.dumps(auth)) + ");")
    app = create_app(settings=SETTINGS, transport=httpx.MockTransport(remote), studio=studio)
    with TestClient(app, raise_server_exceptions=False) as client, patch.dict(TOKENS, {auth["access_token"]: USER_A}), patch.object(studio, "_provider", return_value=provider), patch.dict(os.environ, {"MOBILE_ALLOWED_EMAILS": persona["email"]}):
        def route_handler(route):
            request = route.request
            parsed = urlsplit(request.url)
            if parsed.hostname == "127.0.0.1" and not parsed.path.startswith("/api/"):
                route.continue_(); return
            headers = {"access-control-allow-origin": "*", "access-control-allow-headers": "*"}
            if request.method == "OPTIONS":
                route.fulfill(status=204, headers=headers); return
            if parsed.hostname == "127.0.0.1" and parsed.path.startswith("/api/mobile/"):
                response = client.request(request.method, parsed.path + ("?" + parsed.query if parsed.query else ""), content=request.post_data_buffer,
                                          headers={key: value for key, value in request.headers.items() if key in {"authorization", "content-type"}})
                if response.status_code >= 400:
                    result["errors"].append({"path": parsed.path, "status": response.status_code, "detail": response.json()})
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
            career.fill("\n".join(persona["facts"]))
            page.get_by_role("checkbox", name=re.compile("reviewed these facts", re.I)).check()
            page.get_by_role("button", name="Continue", exact=True).click()
            page.get_by_role("textbox", name=re.compile("^Target roles")).fill(", ".join(persona["roles"]))
            page.get_by_role("textbox", name=re.compile("Preferred countries")).fill(", ".join(persona["locations"]))
            page.get_by_role("button", name="Continue", exact=True).click()
            page.get_by_role("combobox").select_option(persona["remote"])
            page.get_by_role("checkbox").set_checked(persona["sponsorship"])
            page.get_by_role("textbox", name=re.compile("Work authorisation")).fill(persona["constraints"])
            page.get_by_role("button", name="Continue", exact=True).click()
            page.locator("input[type=file]").set_input_files(str(ROOT / persona["resume"]))
            page.get_by_role("button", name="Continue", exact=True).click()
            page.get_by_role("button", name="Create my private workspace", exact=True).click()
            expect(page.get_by_role("navigation", name="Primary navigation")).to_be_visible(timeout=60000)
            result["checks"].append("onboarding_with_real_upload_and_confirmed_facts")
            # Import through the same authenticated API to isolate the repaired
            # preparation path from the separately tested add-role UI.
            response = client.post("/api/mobile/jobs", json=persona["job"], headers={"Authorization": "Bearer " + auth["access_token"]})
            assert response.status_code == 201, response.text
            job_id = response.json()["id"]
            page.reload()
            page.get_by_role("navigation", name="Primary navigation").get_by_role("button", name="Discover", exact=True).click()
            card = page.get_by_role("article").filter(has=page.get_by_role("heading", name=persona["job"]["title"], exact=True)).first
            card.get_by_role("button", name="Application studio", exact=True).click()
            expect(page.get_by_role("region", name="Application Studio")).to_be_visible()
            page.get_by_label("Your eligibility status").select_option("eligible")
            page.get_by_label("Reason for this self-report").fill("Synthetic QA declaration only; not a real person or employer application.")
            page.get_by_role("checkbox", name=re.compile("explicitly confirm this job-specific")).check()
            page.get_by_role("button", name="Save eligibility self-report").click()
            expect(page.get_by_text("Your job-specific self-report was saved.", exact=False)).to_be_visible()
            page.get_by_role("navigation", name="Application preparation steps").get_by_role("button", name="Documents", exact=True).click()
            page.get_by_role("checkbox", name=re.compile("Enable AI assistance")).check()
            page.get_by_role("button", name="Assess role with AI").click()
            expect(page.get_by_text("Role assessment saved.", exact=False)).to_be_visible()
            page.get_by_role("button", name="Prepare draft documents").click()
            expect(page.get_by_text("Draft documents prepared.", exact=False)).to_be_visible()
            page.get_by_role("navigation", name="Application preparation steps").get_by_role("button", name="Documents", exact=True).click()
            expect(page.get_by_role("button", name="Download document")).to_have_count(4)
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
            result["checks"] += ["explicit_eligibility", "real_rank", "real_prepare_four_files", "browser_download", "no_application_submission"]
            result["overflow"] = page.evaluate("document.documentElement.scrollWidth > innerWidth")
            assert not result["overflow"]
            page.screenshot(path=str(OUT / f"{persona['id']}-{width}.png"), full_page=True)
            result["status"] = "passed"
        except Exception as error:
            result["status"] = "failed"
            result["failure"] = str(error)[:1600]
            page.screenshot(path=str(OUT / f"{persona['id']}-{width}-failure.png"), full_page=True)
        finally:
            result["provider_calls"] = provider.calls
            result["artifacts"] = len(remote.tables["artifacts"])
            context.close()
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    personas = json.loads((ROOT / "tests/fixtures/resumes/personas.json").read_text())
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for identifier, width in (("P01", 1440), ("P03", 390), ("P06", 768)):
            result = run_persona(browser, next(p for p in personas if p["id"] == identifier), width)
            results.append(result)
            print(identifier, result["status"], result.get("failure", ""), flush=True)
        browser.close()
    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if all(row["status"] == "passed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
