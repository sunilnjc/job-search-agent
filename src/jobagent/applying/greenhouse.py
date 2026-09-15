"""Conservative browser handler for public Greenhouse application forms."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from jobagent.config import settings
from jobagent.profile import answers
from jobagent.applying.ats import BROWSER_UA


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


def _identity_location() -> tuple[Optional[str], Optional[str]]:
    return _approved("identity.location"), _approved("identity.country")


def _first(page, selector: str):
    locator = page.locator(selector)
    return locator.first if locator.count() else None


def _form_context(page):
    """Return the Greenhouse iframe when an employer wraps the form in its own page."""
    for frame in page.frames:
        if "greenhouse.io" in frame.url and ("job_app" in frame.url or "job-boards" in frame.url):
            return frame
    return page


def _open_form(page) -> object:
    """Open an employer-hosted application shell and return its actual form context."""
    # Employer sites such as Databricks mount an embedded Greenhouse iframe a few seconds
    # after their own shell reaches domcontentloaded.
    for _ in range(5):
        form = _form_context(page)
        if form is not page:
            return form
        page.wait_for_timeout(1_000)
    apply_button = _first(
        page,
        "#apply_button, #filter-apply-handler, a[href*='application'], a[href*='/apply'], "
        "a:has-text('Apply now'), button:has-text('Apply now'), a:has-text('Apply'), button:has-text('Apply')",
    )
    if apply_button:
        apply_button.click(timeout=10_000)
        page.wait_for_timeout(1_000)
    return _form_context(page)


def _has_captcha(page) -> bool:
    return bool(page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha, .h-captcha").count())


def _wait_for_upload(page, timeout_ms: int = 12_000):
    """N26 hydrates its embedded Greenhouse application form after page load.

    `domcontentloaded` only guarantees the N26 shell, not the rendered form. Waiting for
    the upload field prevents a false "not found" before its client-side form appears.
    """
    selector = "input[type='file'][name*='resume' i], input[type='file']"
    locator = page.locator(selector)
    try:
        # A cover-letter upload often sits beside the resume control, so wait on the
        # first match rather than requiring the multi-element locator to be singular.
        locator.first.wait_for(state="attached", timeout=timeout_ms)
    except Exception:  # no form is a normal, reportable application exception
        return None
    return _first(page, selector)


def preflight(job: Mapping[str, object]) -> SubmissionResult:
    """Check that a standard Greenhouse form exposes a resume control before confirmation.

    No fields are filled and no application is created here. This prevents a misleading
    "packet ready" Telegram button for employer-hosted pages that only resemble Greenhouse.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return SubmissionResult("exception", "playwright_not_installed")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=BROWSER_UA)
                page.goto(str(job["final_url"]), wait_until="domcontentloaded", timeout=45_000)
                form = _open_form(page)
                if _has_captcha(form):
                    return SubmissionResult("exception", "captcha_required")
                if not _wait_for_upload(form):
                    return SubmissionResult("exception", "resume_upload_field_not_found")
                return SubmissionResult("ready_for_submission")
            finally:
                browser.close()
    except Exception as exc:
        return SubmissionResult("exception", f"greenhouse_preflight_error:{type(exc).__name__}")


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
    location, country = _identity_location()
    if not all((first_name, last_name, email)):
        return SubmissionResult("exception", "missing_approved_identity_answer")
    if not resume.is_file() or not cover_letter.is_file():
        return SubmissionResult("exception", "application_documents_missing")

    try:
        with sync_playwright() as playwright:
            settings.autopilot_browser_profile.mkdir(parents=True, exist_ok=True)
            browser = playwright.chromium.launch_persistent_context(
                str(settings.autopilot_browser_profile), headless=settings.autopilot_headless,
                user_agent=BROWSER_UA,
            )
            try:
                page = browser.pages[0] if browser.pages else browser.new_page()
                page.goto(str(job["final_url"]), wait_until="domcontentloaded", timeout=45_000)
                form = _open_form(page)
                if _has_captcha(form):
                    return SubmissionResult("exception", "captcha_required")

                values = {
                    "#first_name, input[name='first_name']": first_name,
                    "#last_name, input[name='last_name']": last_name,
                    "#preferred_name, input[name='preferred_name']": first_name,
                    "#email, input[name='email']": email,
                }
                if phone:
                    values["#phone, input[name='phone']"] = phone
                if location:
                    values["#location, input[name='location']"] = location
                for selector, value in values.items():
                    field = _first(form, selector)
                    if field:
                        field.fill(value)
                if country:
                    country_field = _first(form, "#country, select[name='country']")
                    if country_field:
                        tag_name = country_field.evaluate("element => element.tagName.toLowerCase()")
                        if tag_name == "select":
                            country_field.select_option(label=country)
                        else:
                            # Modern embedded Greenhouse forms use a searchable country
                            # combobox rather than a native <select>.
                            country_field.fill(country)
                            country_field.press("ArrowDown")
                            country_field.press("Enter")
                resume_field = _wait_for_upload(form)
                if not resume_field:
                    return SubmissionResult("exception", "resume_upload_field_not_found")
                resume_field.set_input_files(str(resume))
                cover_field = _first(form, "input[type='file'][name*='cover' i]")
                if cover_field:
                    cover_field.set_input_files(str(cover_letter))

                # Handle required checkbox/radio groups as a single question, rather
                # than prompting once per option. Explicit choices are saved by their
                # visible group question in the private approval library.
                groups = form.locator("fieldset[id^='question_']").evaluate_all(
                    """els => els.map(e => ({
                      id: e.id,
                      label: (e.innerText || '').split('\\n')[0].trim(),
                      required: !!e.querySelector('input[required]'),
                      options: [...e.querySelectorAll('input[type=checkbox], input[type=radio]')].map(input => ({
                        id: input.id,
                        label: (document.querySelector(`label[for='${input.id}']`) || {}).innerText || ''
                      }))
                    }))"""
                )
                for group in groups:
                    label = str(group.get("label") or "").strip()
                    options = [str(option.get("label") or "") for option in group.get("options", [])]
                    choice = answers.choice_for_question(label, options)
                    if not choice and group.get("required"):
                        return SubmissionResult("exception", "required_question_needs_input:" + (label or group["id"]))
                    if choice:
                        option = next((item for item in group["options"] if item.get("label") == choice), None)
                        if option:
                            # Greenhouse checkbox IDs can contain [] which is not valid
                            # CSS in a raw #id selector.
                            form.locator(f'[id="{option["id"]}"]').check()

                questions = form.locator("input[id^='question_'], textarea[id^='question_'], select[id^='question_']").evaluate_all(
                    """els => els.filter(e => !e.closest('fieldset')).map(e => ({
                      id: e.id, tag: e.tagName, type: e.type, required: !!e.required,
                      label: (document.querySelector(`label[for='${e.id}']`) || {}).innerText || ''
                    }))"""
                )
                for question in questions:
                    label = str(question.get("label") or "").strip()
                    required_question = bool(question.get("required")) or label.endswith("*")
                    answer = answers.answer_for(label) if label else None
                    custom = answers.custom_answer_for(label) if label else None
                    if required_question and not custom and (answer is None or answer.needs_input):
                        return SubmissionResult("exception", "required_question_needs_input:" + (label or question["id"]))
                    field = form.locator(f'[id="{question["id"]}"]')
                    if str(question["tag"]).lower() == "select":
                        options = field.locator("option").all_text_contents()
                        choice = answers.choice_for_question(label, options)
                        if choice:
                            field.select_option(label=choice)
                        elif answer and answer.value:
                            field.select_option(label=answer.value)
                    elif custom:
                        field.fill(custom)
                    elif answer and answer.value:
                        field.fill(answer.value)

                required = form.locator("input[required], textarea[required], select[required]").evaluate_all(
                    "els => els.filter(e => e.type !== 'hidden' && !e.value && e.type !== 'file').map(e => e.name || e.id || e.type)"
                )
                if required:
                    return SubmissionResult("exception", "required_question_needs_input:" + ",".join(required[:3]))
                if _has_captcha(form):
                    return SubmissionResult("exception", "captcha_required")
                submit_button = _first(form, "input[type='submit'], button[type='submit']")
                if not submit_button:
                    return SubmissionResult("exception", "submit_button_not_found")
                submit_button.click(timeout=10_000)
                page.wait_for_timeout(2_000)
                text = form.locator("body").inner_text().lower()
                if "thank you" in text or "application submitted" in text or "application received" in text:
                    return SubmissionResult("submitted")
                return SubmissionResult("exception", "submission_confirmation_not_detected")
            finally:
                browser.close()
    except Exception as exc:  # form variations/network failures are exceptions, never successes
        return SubmissionResult("exception", f"greenhouse_error:{type(exc).__name__}")
