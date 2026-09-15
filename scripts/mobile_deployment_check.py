#!/usr/bin/env python3
"""Read-only, credential-free edge check. Offline unless --execute is supplied.

Tests only fixed public GET routes; never follows redirects, sends credentials,
or reads founder response bodies. A passing result is a routing smoke check,
NOT proof of authenticated ownership, document privacy or email delivery.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class Probe:
    path: str
    status: int
    passed: bool


PROTECTED_PATHS = ("/admin", "/api/jobs", "/api/answers", "/api/status/summary",
                   "/api", "/api/mobile-extra", "/api/mobileevil")
PATHS = (*PROTECTED_PATHS, "/beta", "/api/mobile/health", "/api/mobile/bootstrap")


def validate_origin(origin: str) -> str:
    parts = urlsplit(origin)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/")
            or parts.query or parts.fragment or any(c.isspace() or c == "\\" for c in origin)):
        raise ValueError("An HTTPS origin without credentials or a path is required.")
    _ = parts.port
    return origin.rstrip("/")


def protected(response: httpx.Response) -> bool:
    if response.status_code in (401, 403):
        return True
    location = urlsplit(response.headers.get("location", ""))
    return (response.status_code in (302, 303, 307, 308) and location.scheme == "https"
            and bool(location.hostname) and location.hostname.endswith(".cloudflareaccess.com")
            and location.path.startswith("/cdn-cgi/access/login/")
            and location.username is None and location.password is None)


def run(origin: str, *, transport=None) -> list[Probe]:
    origin = validate_origin(origin)
    results = []
    with httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False,
                      trust_env=False, transport=transport) as client:
        for path in PATHS:
            status, passed = 0, False
            try:
                with client.stream("GET", origin + path, headers={"Cache-Control": "no-cache"}) as response:
                    status = response.status_code
                    if path in PROTECTED_PATHS:
                        passed = protected(response)
                    elif path == "/beta":
                        passed = status == 200 and response.headers.get("content-type", "").startswith("text/html")
                    elif path == "/api/mobile/bootstrap":
                        passed = status == 401
                    elif status == 200:
                        # Only the deliberately public minimal health body is read.
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            if len(body) + len(chunk) > 1024:
                                break
                            body.extend(chunk)
                        else:
                            import json
                            passed = json.loads(body) == {"status": "ok", "service": "job-pursuit-mobile"}
            except (httpx.HTTPError, ValueError):
                # No exception text, response bodies or redirect tokens in output.
                passed = False
            results.append(Probe(path, status, passed))
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--origin", default="https://www.thejobpursuit.com")
    args = parser.parse_args(argv)
    if not args.execute:
        print("OFFLINE PLAN: public admin/API access denial, beta HTML, mobile health and unauthenticated mobile denial. No requests made.")
        return 0
    try:
        results = run(args.origin)
    except ValueError:
        print("REFUSED: invalid HTTPS origin.")
        return 2
    for result in results:
        print(("PASS" if result.passed else "FAIL") + f": {result.path} HTTP {result.status}")
    print("Routing smoke check only; authenticated user/device acceptance is separate.")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
