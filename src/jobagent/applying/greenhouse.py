"""Conservative browser handler for public Greenhouse application forms."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from jobagent.config import settings
from jobagent.profile import answers


@dataclass(frozen=True)
class SubmissionResult:
    state: str
    reason: Optional[str] = None


def _approved(field: str) -> Optional[str]:
    return answers.get_field(field).value


def _identity() -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    full_name = _approved("identity.full_name")
    parts = (full_name or "").split(maxsplit=1)
    return (
        parts[0] if parts else None,
        parts[1] if len(parts) > 1 else None,
        _approved("identity.email"),
        _approved("identity.phone"),
    )


def _first(page, selector: str):
    locator = page.locator(selector)
    return locator.first if locator.count() else None


def submit(job: Mapping[str, object], resume: Path, cover_letter: Path) -> SubmissionResult:
    """Submit one Greenhouse form after Telegram has received a confirm action.

    This function deliberately supports only the standard identity/upload fields. Any required
    custom question is an exception, not something an LLM gets to guess.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return SubmissionResult("exception", "playwright_not_installed")

    first_name, last_name, email, phone = _identity()
    if not all((first_name, last_name, email)):
        return SubmissionResult("exception", "missing_approved_identity_answer")
    if not resume.is_file() or not cover_letter.is_file():
        return SubmissionResult("exception", "application_documents_missing")

    try:
        with sync_playwright() as playwright:
            settings.autopilot_browser_profile.mkdir(parents=True, exist_ok=True)
            browser = playwright.chromium.launch_persistent_context(
                str(settings.autopilot_browser_profile), headless=settings.autopilot_headless
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(str(job["final_url"]), wait_until="domcontentloaded", timeout=45_000)
                apply_button = _first(
                    page,
                    "#apply_button, a[href*='application'], a[href*='/apply'], a:has-text('Apply'), button:has-text('Apply')",
                )
                if apply_button:
                    apply_button.click(timeout=10_000)
                    page.wait_for_load_state("domcontentloaded", timeout=20_000)
                if page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha").count():
                    return SubmissionResult("exception", "captcha_required")

                values = {
                    "#first_name, input[name='first_name']": first_name,
                    "#last_name, input[name='last_name']": last_name,
                    "#email, input[name='email']": email,
                }
                if phone:
                    values["#phone, input[name='phone']"] = phone
                for selector, value in values.items():
                    field = _first(page, selector)
                    if field:
                        field.fill(value)
                resume_field = _first(page, "input[type='file'][name*='resume' i], input[type='file']")
                if not resume_field:
                    return SubmissionResult("exception", "resume_upload_field_not_found")
                resume_field.set_input_files(str(resume))
                cover_field = _first(page, "input[type='file'][name*='cover' i]")
                if cover_field:
                    cover_field.set_input_files(str(cover_letter))

                # Greenhouse custom questions have stable question_* IDs. Inspect their
                # visible labels before generic required controls so Telegram can tell Sunil
                # the real question (e.g. right-to-work) rather than "text,text,text".
                questions = page.locator("[id^='question_']").evaluate_all(
                    """els => els.map(e => ({
                      id: e.id, tag: e.tagName, required: !!e.required,
                      label: (document.querySelector(`label[for='${e.id}']`) || {}).innerText || ''
                    }))"""
                )
                for question in questions:
                    label = str(question.get("label") or "").strip()
                    required_question = bool(question.get("required")) or label.endswith("*")
                    answer = answers.answer_for(label) if label else None
                    if required_question and (answer is None or answer.needs_input):
                        return SubmissionResult("exception", "required_question_needs_input:" + (label or question["id"]))
                    if answer and answer.value:
                        field = page.locator(f"#{question['id']}")
                        if str(question["tag"]).lower() == "select":
                            field.select_option(label=answer.value)
                        else:
                            field.fill(answer.value)

                required = page.locator("input[required], textarea[required], select[required]").evaluate_all(
                    "els => els.filter(e => e.type !== 'hidden' && !e.value && e.type !== 'file').map(e => e.name || e.id || e.type)"
                )
                if required:
                    return SubmissionResult("exception", "required_question_needs_input:" + ",".join(required[:3]))
                if page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha").count():
                    return SubmissionResult("exception", "captcha_required")
                submit_button = _first(page, "input[type='submit'], button[type='submit']")
                if not submit_button:
                    return SubmissionResult("exception", "submit_button_not_found")
                submit_button.click(timeout=10_000)
                page.wait_for_timeout(2_000)
                text = page.locator("body").inner_text().lower()
                if "thank you" in text or "application submitted" in text or "application received" in text:
                    return SubmissionResult("submitted")
                return SubmissionResult("exception", "submission_confirmation_not_detected")
            finally:
                browser.close()
    except Exception as exc:  # form variations/network failures are exceptions, never successes
        return SubmissionResult("exception", f"greenhouse_error:{type(exc).__name__}")
