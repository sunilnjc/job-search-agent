"""Additional isolated UI failures plus read-only public landing observation."""
import json
import re
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from launch_browser_eval import Boundary, PERSONAS, URL, OUT, ROOT, onboard, nav, advance


def local_cases(browser):
    results = []
    b = Boundary(browser, PERSONAS[1], signed=False)
    try:
        b.page.goto(URL)
        b.page.get_by_role('textbox', name='Email address', exact=True).fill('invalid-email')
        b.page.get_by_role('button', name='Create account with email', exact=True).click()
        results.append({'case': 'invalid email', 'native_validation_blocks': b.page.locator('input[type=email]').evaluate('(e)=>!e.checkValidity()'), 'auth_requests': len(b.requests)})
        b.page.get_by_role('textbox', name='Email address', exact=True).fill('synthetic@example.test')
        b.page.get_by_role('button', name='Create account with email', exact=True).click()
        expect(b.page.get_by_role('alert')).to_be_visible()
        results.append({'case': 'email throttling', 'message': b.page.get_by_role('alert').inner_text(), 'email_actually_sent': False})
    finally:
        b.close()
    b = Boundary(browser, PERSONAS[0])
    try:
        b.fail_upload = True
        try:
            onboard(b, PERSONAS[0])
        except AssertionError:
            pass
        results.append({'case': 'onboarding storage failure', 'profile_marked_complete': bool(b.rows['profiles'][0].get('onboarding_completed_at')), 'resumes_saved': len(b.rows['resumes']), 'visible_error': b.page.get_by_role('alert').inner_text()})
        b.page.reload()
        expect(b.page.get_by_role('heading', name='Your next move, Aarav.')).to_be_visible()
        results[-1]['reload_skips_upload_recovery'] = True
        b.page.screenshot(path=str(OUT/'onboarding-partial-save.png'), full_page=True)
    finally:
        b.close()
    b = Boundary(browser, PERSONAS[5])
    try:
        onboard(b, PERSONAS[5]); nav(b.page, 'Studio')
        b.page.locator('input[type=file]').set_input_files(str(ROOT / PERSONAS[5]['resume']))
        expect(b.page.get_by_text('Uploaded '+Path(PERSONAS[5]['resume']).name+'.', exact=True)).to_be_visible()
        results.append({'case': 'duplicate source upload', 'same_filename_metadata_rows': len(b.rows['resumes']), 'actual_storage': 'stub'})
        b.page.locator('input[type=file]').set_input_files({'name': 'oversized.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.7\n' + b'X' * (9 * 1024 * 1024)})
        expect(b.page.get_by_text('Uploaded oversized.pdf.', exact=True)).to_be_visible()
        results.append({'case': 'oversized invalid PDF', 'client_accepted_over_9_MiB': True, 'actual_storage': 'stub; does not prove hosted acceptance'})
    finally:
        b.close()
    return results


def public_landing(browser):
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    blocked_writes = []
    def read_only(route):
        if route.request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            blocked_writes.append(route.request.method)
            route.abort()
        else:
            route.continue_()
    context.route('**/*', read_only)
    page = context.new_page(); page.set_default_timeout(15000)
    start = time.monotonic()
    response = page.goto('https://www.thejobpursuit.com/beta', wait_until='domcontentloaded')
    expect(page.get_by_role('button', name='Create account with email', exact=True)).to_be_visible()
    elapsed = time.monotonic() - start
    text = page.locator('body').inner_text()
    result = {'http_status': response.status, 'url': page.url.split('?')[0], 'first_auth_button_seconds': round(elapsed, 2), 'horizontal_overflow': page.evaluate('document.documentElement.scrollWidth > innerWidth+1'), 'visible_text': text, 'non_read_requests_blocked': blocked_writes, 'browser': 'fresh Chromium context; no credentials, no emails'}
    page.screenshot(path=str(OUT/'public-anonymous-mobile.png'), full_page=True)
    context.close()
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        results = {'local': local_cases(browser)}
        try:
            results['public'] = public_landing(browser)
        except Exception as error:
            results['public'] = {'not_completed': type(error).__name__}
        (OUT/'additional-edge-results.json').write_text(json.dumps(results, indent=2)+'\n')
        print(json.dumps(results, indent=2))
        browser.close()


if __name__ == '__main__':
    main()
