#!/usr/bin/env python3
"""Interactive, real deployed Chromium acceptance runner; never stubs app APIs.

Uses an isolated context, not the founder browser session. Reads bounded JSON
commands from stdin. A loopback-only handoff form accepts a magic link without
printing it or placing tokens in shell arguments. No external application forms
or payment submissions are supported. Evidence omits query strings and bodies.
Run with a test email that you own. Signup intentionally sends one auth email.
"""
import argparse
import json
import os
import queue
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', required=True)
    parser.add_argument('--origin', default='https://www.thejobpursuit.com')
    parser.add_argument('--out', type=Path, default=Path('output/launch-closure-live'))
    parser.add_argument('--handoff-port', type=int, default=5192)
    args = parser.parse_args()
    # The auth host issues the sign-in link and is deployment-specific, so it is
    # not baked into the source. It is required on its own: the app origin is a
    # different host and must not stand in for it, or an unset variable would
    # silently leave the handoff accepting links the reviewer never approved.
    auth_host = os.environ.get('JOBPURSUIT_AUTH_HOST', '').strip()
    if not auth_host:
        parser.error('Set JOBPURSUIT_AUTH_HOST to the reviewed auth hostname before running.')
    allowed_hosts = tuple(dict.fromkeys(
        host for host in (auth_host, urlsplit(args.origin).hostname) if host))
    if args.origin != 'https://www.thejobpursuit.com':
        parser.error('This deployed acceptance runner is pinned to the approved website.')
    args.out.mkdir(parents=True, exist_ok=True)
    tasks = queue.Queue()
    handoff = '/handoff-' + secrets.token_hex(16)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused): pass
        def do_GET(self):
            if self.path != handoff:
                self.send_error(404); return
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(b'<h1>Golden customer sign-in handoff</h1><p>Paste only the test customer magic link copied from the email. It is not logged.</p><form method="post"><label>Test sign-in link<textarea name="link" required></textarea></label><button>Continue isolated test</button></form>')
        def do_POST(self):
            if self.path != handoff or self.headers.get('Origin') != f'http://127.0.0.1:{args.handoff_port}':
                self.send_error(403); return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 16384:
                self.send_error(400); return
            link = parse_qs(self.rfile.read(length).decode()).get('link', [''])[0].strip()
            parsed = urlsplit(link)
            if parsed.scheme != 'https' or parsed.hostname not in allowed_hosts or parsed.username or parsed.password:
                self.send_error(400); return
            tasks.put({'action': '_login', 'link': link})
            self.send_response(200); self.send_header('Cache-Control','no-store'); self.end_headers()
            self.wfile.write(b'Test link accepted. No credentials logged.')
    server = HTTPServer(('127.0.0.1', args.handoff_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def stdin_reader():
        for line in sys.stdin:
            try:
                if len(line) > 150000: raise ValueError()
                tasks.put(json.loads(line))
            except (ValueError, TypeError): print('Invalid command', flush=True)
    threading.Thread(target=stdin_reader, daemon=True).start()
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width':1440,'height':1000}, accept_downloads=True, service_workers='block')
        page = context.new_page()
        page.set_default_timeout(10000)
        def response_event(response):
            parsed = urlsplit(response.url)
            if parsed.hostname == 'www.thejobpursuit.com' and parsed.path.startswith('/api/mobile'):
                results.append({'method':response.request.method,'path':parsed.path,'status':response.status})
        page.on('response', response_event)
        page.goto(args.origin + '/beta')
        page.get_by_label('Email address', exact=True).fill(args.email)
        page.get_by_role('button', name='Create account with email', exact=True).click()
        page.get_by_role('heading', name='Check your inbox', exact=True).wait_for()
        print(json.dumps({'signup':'email_sent','handoff':f'http://127.0.0.1:{args.handoff_port}{handoff}'}), flush=True)
        count = 0
        while True:
            command = tasks.get()
            action = command.get('action')
            try:
                if action == 'stop': break
                if action == '_login':
                    page.goto(command['link'], wait_until='domcontentloaded')
                    page.wait_for_url(args.origin + '/beta*', timeout=30000)
                elif action == 'state':
                    print(page.locator('body').inner_text()[:18000], flush=True)
                elif action == 'click':
                    locator = page.get_by_role(command.get('role','button'), name=command['name'], exact=command.get('exact',True))
                    locator.nth(command.get('index',0)).click()
                elif action == 'fill': page.get_by_label(command['label'], exact=command.get('exact',True)).fill(command['value'])
                elif action == 'check': page.get_by_label(command['label'], exact=command.get('exact',False)).set_checked(command.get('value',True))
                elif action == 'select': page.get_by_role('combobox', name=re.compile('^' + re.escape(command['label']))).select_option(command['value'])
                elif action == 'upload': page.get_by_label(command['label'], exact=True).set_input_files(command['file'])
                elif action == 'reload': page.reload()
                elif action == 'download':
                    with page.expect_download() as info:
                        page.get_by_role('button', name=command['name'], exact=command.get('exact',True)).nth(command.get('index',0)).click()
                    download = info.value
                    target = args.out / Path(download.suggested_filename).name
                    download.save_as(target)
                    print(json.dumps({'download':target.name,'bytes':target.stat().st_size}), flush=True)
                elif action == 'screenshot':
                    count += 1
                    page.screenshot(path=str(args.out / f'step-{count:03d}.png'), full_page=True)
                    print(json.dumps({'screenshot':str(args.out / f'step-{count:03d}.png')}), flush=True)
                else: raise ValueError('Unsupported action')
                (args.out / 'http-results.json').write_text(json.dumps(results, indent=2))
                print(json.dumps({'action':action,'completed':True}), flush=True)
            except Exception as exc:
                # Playwright exceptions may embed the magic link; never print them.
                print(json.dumps({'action':action,'completed':False,'error_type':type(exc).__name__}), flush=True)
                # Do not keep executing queued form actions on the wrong step
                # after one locator/validation failure. Inspect and resume.
                while not tasks.empty():
                    try: tasks.get_nowait()
                    except queue.Empty: break
        context.close(); browser.close()
    server.shutdown()


if __name__ == '__main__': main()
