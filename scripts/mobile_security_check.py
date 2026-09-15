#!/usr/bin/env python3
"""Read-only, scoped source check; no network calls or third-party dependencies.

Default: inspect Git-tracked and non-ignored candidate files under ios/ and
src/jobagent/mobile/. --candidate adds a named file in those roots, even if
ignored (for example Config/Local.xcconfig). No traversal outside those roots.
Only paths, line numbers, rule IDs and counts are printed, never source values.
This is a heuristic source check, not a credential audit or binary scanner.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence


ROOTS = ("ios", "src/jobagent/mobile")
GENERATED = frozenset({
    "deriveddata", "build", ".build", "pods", "carthage", "node_modules",
    "__pycache__", ".git", "xcuserdata", ".swiftpm",
})
BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".heic", ".ico", ".pdf", ".docx",
    ".zip", ".ipa", ".a", ".o", ".dylib", ".so", ".pyc", ".ttf", ".woff2",
    ".car", ".db", ".sqlite", ".sqlite3", ".mp4", ".mov", ".xcassetsbin",
})
MAX_BYTES = 4 * 1024 * 1024
# Public sb_publishable_ and legacy JWT role=anon keys are deliberately allowed.
PREFIX = re.compile(
    r"(?<![A-Za-z0-9_])(?P<prefix>sk-(?:proj-|ant-|svcacct-|or-v1-)?|"
    r"sb_secret_|gsk_|xai-|gh[pousr]_|github_pat_|xox[baprs]-|AIza|AKIA|ASIA)"
    r"(?P<body>[A-Za-z0-9_\-]+)"
)
JWT = re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)(?<![\w])(?:[\"']?)(?:SUPABASE[_-]?)?"
    r"(?:SERVICE[_-]?ROLE(?:[_-]?KEY)?|SECRET[_-]?KEY)"
    r"[\"']?[ \t]*(?::[ \t]*(?:String|str|Optional\[str\])[ \t]*=|="
    r"|:(?![ \t]*(?:String|str|Optional\[str\])[ \t]*(?:[,)]|$)))"
    r"[ \t]*(?P<value>[^\r\n]+)"
)
PLIST_SECRET = re.compile(
    r"(?is)<key>\s*(?:SUPABASE[_-]?)?(?:SERVICE[_-]?ROLE(?:[_-]?KEY)?|"
    r"SECRET[_-]?KEY)\s*</key>\s*<string>(?P<value>.*?)</string>"
)
PLACEHOLDER = re.compile(
    r"(?i)(?:example|placeholder|redacted|replace[-_]me|changeme|"
    r"your[-_](?:service[-_]role[-_]key|secret[-_]key|api[-_]key)|x{8,})"
)
REFERENCE = re.compile(
    r"(?:os\.(?:getenv|environ(?:\.get)?)\s*[\[(]|"
    r"(?:getenv|env)\s*\(|(?:settings|config|self)\.[A-Za-z_]\w*\s*$)"
)


@dataclass(frozen=True, order=True)
class Finding:
    line: int
    rule: str


def is_placeholder(value: str) -> bool:
    value = value.strip().rstrip(",;").strip().strip("\"'")
    prefixed = PREFIX.fullmatch(value)
    if prefixed and PLACEHOLDER.fullmatch(prefixed.group("body")):
        return True
    return (
        not value
        or value in {"None", "nil", "null"}
        or PLACEHOLDER.fullmatch(value) is not None
        or re.fullmatch(r"<[-\w ]+>|\$\{[A-Za-z_]\w*\}|\$\([A-Za-z_]\w*\)", value) is not None
    )


def scan_text(source: str) -> list[Finding]:
    """Return locations/rules only; never retain matched values in findings."""
    findings: set[Finding] = set()

    def record(offset: int, rule: str) -> None:
        findings.add(Finding(source.count("\n", 0, offset) + 1, rule))

    for match in PREFIX.finditer(source):
        if not is_placeholder(match.group("body")):
            record(match.start(), "prohibited-key-prefix")
    for match in PRIVATE_KEY.finditer(source):
        record(match.start(), "private-key-material")
    for match in JWT.finditer(source):
        payload = match.group().split(".")[1]
        try:
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (ValueError, UnicodeError, binascii.Error):
            continue
        if isinstance(claims, dict) and claims.get("role") == "service_role":
            record(match.start(), "supabase-service-role-jwt")
        elif isinstance(claims, dict) and claims.get("role") == "authenticated":
            record(match.start(), "hardcoded-user-session")
    for pattern in (SENSITIVE_ASSIGNMENT, PLIST_SECRET):
        for match in pattern.finditer(source):
            # A denial check or comment mentioning service_role is not a key.
            line_start = source.rfind("\n", 0, match.start()) + 1
            if source[line_start:match.start()].lstrip().startswith(("#", "//", "*", "<!--")):
                continue
            value = match.group("value").split(" #", 1)[0].strip()
            if value.startswith("=") or REFERENCE.match(value):
                continue
            if not is_placeholder(value):
                record(match.start(), "service-role-or-secret-assignment")
    return sorted(findings)


def scoped_path(name: str) -> Optional[PurePosixPath]:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    if not any(relative.parts[:len(PurePosixPath(root).parts)] == PurePosixPath(root).parts for root in ROOTS):
        return None
    if relative.as_posix() in ROOTS:
        return None
    return relative


def skip_path(relative: PurePosixPath) -> bool:
    return (
        any(part.lower() in GENERATED for part in relative.parts)
        or relative.suffix.lower() in BINARY_SUFFIXES
    )


def decode_source(data: bytes) -> Optional[str]:
    # XML plists may be UTF-16; binary plists and other binaries are out of scope.
    if data.startswith(b"bplist"):
        return None
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        if b"\x00" in data:
            return None
        return data.decode("utf-8-sig")
    except UnicodeError:
        return None


def candidate_paths(repo: Path, extra: Sequence[str]) -> list[PurePosixPath]:
    listing = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *ROOTS],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ).stdout
    names = {name.decode("utf-8", errors="surrogateescape") for name in listing.split(b"\x00") if name}
    for name in extra:
        if scoped_path(name) is None:
            raise ValueError("Candidate is outside the allowed source roots")
        names.add(name)
    return sorted({item for name in names if (item := scoped_path(name)) is not None})


def check(repo: Path, extra: Sequence[str]) -> int:
    try:
        paths = candidate_paths(repo, extra)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("ERROR: cannot enumerate scoped source files or invalid candidate path.")
        return 2
    scanned = skipped = failures = errors = 0
    for relative in paths:
        if skip_path(relative):
            skipped += 1
            continue
        target = repo.joinpath(*relative.parts)
        label = json.dumps(relative.as_posix(), ensure_ascii=True)
        try:
            # Never follow a symlink, including a directory symlink, to private files.
            if any(parent.is_symlink() for parent in (target, *target.parents) if parent != repo):
                raise ValueError("Symlink outside scan contract")
            if not target.exists() and relative.as_posix() not in extra:
                skipped += 1  # A tracked file deleted in the working tree.
                continue
            if not target.is_file():
                raise ValueError("Candidate must be a regular file")
            with target.open("rb") as handle:
                data = handle.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Oversize candidate needs manual review")
            source = decode_source(data)
            if source is None:
                skipped += 1
                continue
        except (OSError, ValueError):
            print(f"ERROR {label}: unreadable, symlink, non-file, or oversized source; review required.")
            errors += 1
            continue
        scanned += 1
        for finding in scan_text(source):
            print(f"FAIL {label}:{finding.line} {finding.rule}")
            failures += 1
    print(f"Scoped mobile check: {scanned} text files, {skipped} skipped, {failures} findings, {errors} errors.")
    if failures:
        return 1
    if errors or not scanned:
        print("INCOMPLETE: fix scan errors or supply mobile/iOS source before claiming a pass.")
        return 2
    print("PASS: no prohibited material detected in inspected source; live security is not verified.")
    return 0


def self_test() -> int:
    """In-memory synthetic fixtures only: no files, credentials, models, or network."""
    import io
    import unittest
    from contextlib import redirect_stdout
    from unittest.mock import patch

    def jwt(role: str) -> str:
        encode = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
        return encode({"alg": "HS256"}) + "." + encode({"role": role}) + ".fixture"

    class SecurityTests(unittest.TestCase):
        def test_prohibited_prefixes(self):
            for prefix in ("sk-proj-", "sk-ant-", "sk-", "sb_secret_", "gsk_", "xai-", "ghp_", "github_pat_", "AIza", "AKIA"):
                with self.subTest(prefix=prefix):
                    self.assertEqual([f.rule for f in scan_text(prefix + "synthetic123456789")], ["prohibited-key-prefix"])

        def test_public_keys(self):
            self.assertEqual(scan_text('SUPABASE_ANON_KEY = "' + jwt("anon") + '"\nsb_publishable_synthetic123456'), [])

        def test_service_role_jwt(self):
            self.assertEqual([f.rule for f in scan_text(jwt("service_role"))], ["supabase-service-role-jwt"])

        def test_user_session(self):
            self.assertEqual([f.rule for f in scan_text(jwt("authenticated"))], ["hardcoded-user-session"])

        def test_invalid_jwt(self):
            self.assertEqual(scan_text("eyJinvalid.invalid.fixture"), [])

        def test_placeholders(self):
            for value in ('""', '"<service-role-key>"', '"YOUR_SERVICE_ROLE_KEY"', '"${SERVER_SECRET}"', '"$(SERVER_SECRET)"', '"REPLACE_ME"', '"sk-proj-placeholder"'):
                self.assertEqual(scan_text("SUPABASE_SERVICE_ROLE_KEY = " + value), [])

        def test_placeholder_word_not_blanket_allowlist(self):
            self.assertTrue(scan_text("sk-proj-example123456789"))

        def test_config_assignment(self):
            self.assertEqual([f.rule for f in scan_text('let supabaseServiceRoleKey: String = "synthetic-value"')], ["service-role-or-secret-assignment"])

        def test_type_annotations_are_not_credentials(self):
            self.assertEqual(scan_text('def __init__(self, secret_key: str, webhook_secret: str, *, portal_configuration: str = "",'), [])
            self.assertEqual(scan_text('secret_key: Optional[str]'), [])
            self.assertTrue(scan_text('def create(secret_key: str = "synthetic-value"):'))
            self.assertTrue(scan_text('secret_key: "synthetic-value"'))

        def test_plist_assignment(self):
            self.assertTrue(scan_text('<key>SUPABASE_SERVICE_ROLE_KEY</key>\n<string>synthetic-value</string>'))

        def test_safety_comments_and_environment_references(self):
            self.assertEqual(scan_text('# service_role = prohibited\n// Never use service_role\nif role == "service_role":\n    deny()\nSUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")'), [])

        def test_private_key(self):
            self.assertEqual([f.rule for f in scan_text("-----BEGIN RSA PRIVATE KEY-----")], ["private-key-material"])

        def test_findings_are_redacted(self):
            result = scan_text("\n" + "sk-proj-synthetic123456789")
            self.assertEqual(result, [Finding(2, "prohibited-key-prefix")])
            self.assertNotIn("synthetic", repr(result))

        def test_scope(self):
            for name in (".env", "docs/private.md", "ios/../.env", "/tmp/ios/key", "src/jobagent/mobile-other/key", "ios"):
                self.assertIsNone(scoped_path(name))
            for name in ("ios/JobPursuit/Config/Local.xcconfig", "src/jobagent/mobile/app.py"):
                self.assertIsNotNone(scoped_path(name))

        def test_generated_and_binary_paths(self):
            for name in ("ios/DerivedData/source.swift", "ios/build/a.swift", "src/jobagent/mobile/__pycache__/a.pyc", "ios/Assets/a.png"):
                self.assertTrue(skip_path(PurePosixPath(name)))
            self.assertFalse(skip_path(PurePosixPath("ios/JobPursuit/Config/Example.xcconfig")))

        def test_decode(self):
            self.assertIsNone(decode_source(b"bplist00binary"))
            self.assertIsNone(decode_source(b"\x00binary"))
            self.assertEqual(decode_source("hello".encode("utf-16")), "hello")

        def test_git_scope_and_deduplication(self):
            with patch("subprocess.run") as run:
                run.return_value.stdout = b"ios/App.swift\0ios/App.swift\0src/jobagent/mobile/app.py\0"
                paths = candidate_paths(Path("/synthetic/repo"), ["ios/Config/Local.xcconfig"])
                self.assertEqual(len(paths), 3)
                self.assertEqual(run.call_args.args[0][-3:], ["--", *ROOTS])

        def test_empty_scan_fails_closed(self):
            with patch("subprocess.run") as run, redirect_stdout(io.StringIO()):
                run.return_value.stdout = b""
                self.assertEqual(check(Path("/synthetic/repo"), []), 2)

        def test_rejected_candidate_not_echoed(self):
            with patch("subprocess.run") as run, redirect_stdout(io.StringIO()) as output:
                run.return_value.stdout = b""
                result = check(Path("/synthetic/repo"), ["/private/sensitive-name"])
            self.assertEqual(result, 2)
            self.assertNotIn("sensitive-name", output.getvalue())

    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(SecurityTests))
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", action="append", default=[], metavar="REPO_RELATIVE_FILE", help="Also inspect this explicitly named file within the allowed roots, even if ignored.")
    parser.add_argument("--self-test", action="store_true", help="Run only synthetic in-memory checks (no network or model calls).")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    return check(Path(__file__).resolve().parent.parent, args.candidate)


if __name__ == "__main__":
    sys.exit(main())
