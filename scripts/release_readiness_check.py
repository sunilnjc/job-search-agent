#!/usr/bin/env python3
"""Credential-free paired web/API release probe; never authorizes a launch.

Default is offline. --execute reads only public version and beta HTML, with no
redirects, credentials, workspace reads, database writes or model calls.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jobagent.mobile.release import CORE_FLOW_CONTRACT
from mobile_deployment_check import validate_origin


class ContractParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.contracts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name") == "job-pursuit-workflow":
            self.contracts.append(attrs.get("content"))


def run(origin, *, transport=None):
    origin = validate_origin(origin)
    checks = []
    with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False,
                      trust_env=False, transport=transport) as client:
        for path, limit in (("/api/mobile/version", 1024), ("/beta", 256 * 1024)):
            result = {"path": path, "status": 0, "contract_matches": False}
            try:
                client.cookies.clear()
                with client.stream("GET", origin + path, headers={"Cache-Control": "no-cache"}) as response:
                    result["status"] = response.status_code
                    expected_type = "application/json" if path.endswith("version") else "text/html"
                    if response.status_code != 200 or not response.headers.get("content-type", "").startswith(expected_type):
                        checks.append(result)
                        continue
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(body) + len(chunk) > limit:
                            break
                        body.extend(chunk)
                    else:
                        if path.endswith("version"):
                            result["contract_matches"] = json.loads(body) == {
                                "service": "job-pursuit-mobile", "workflow_contract": CORE_FLOW_CONTRACT}
                        else:
                            parser = ContractParser()
                            parser.feed(body.decode("utf-8"))
                            result["contract_matches"] = parser.contracts == [CORE_FLOW_CONTRACT]
            except (httpx.HTTPError, ValueError, UnicodeError):
                pass  # Never report raw server bodies, redirect URLs or errors.
            checks.append(result)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--origin", default="https://www.thejobpursuit.com")
    args = parser.parse_args()
    if not args.execute:
        print("OFFLINE: no requests made. Checks paired web/API protocol identity only.")
        return 0
    try:
        checks = run(args.origin)
    except ValueError:
        print("REFUSED: an HTTPS origin without credentials or paths is required.")
        return 2
    print(json.dumps({"checks": checks, "scope": "version identity only; authenticated journey and hosted schema remain separate gates"}))
    return 0 if all(item["contract_matches"] for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
