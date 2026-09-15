#!/usr/bin/env python3
"""Bounded, dependency-free host XCTest fallback; no app/device installation.

Uses the actual native model, networking, state and test source by symlink.
The iOS-bundle packaging assertion explicitly skips on macOS; this is not UI or
authenticated API acceptance. All network tests use URLProtocol/memory fixtures.
"""
import json
from pathlib import Path
import re
import tempfile

from native_test_runner import PROJECT, expected_count, run


def main():
    artifacts = Path(tempfile.mkdtemp(prefix="job-pursuit-native-host-"))
    print(f"Artifacts: {artifacts}", flush=True)
    (artifacts / "Sources").mkdir()
    (artifacts / "Tests").mkdir()
    for name in ("Models.swift", "Networking.swift", "StorageRecovery.swift", "AppStore.swift", "PreviewRecoveryService.swift"):
        (artifacts / "Sources" / name).symlink_to(PROJECT / "Sources" / name)
    for source in (PROJECT / "Tests").glob("*.swift"):
        (artifacts / "Tests" / source.name).symlink_to(source)
    (artifacts / "Package.swift").write_text('''// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "NativeHostChecks", platforms: [.macOS(.v13)], targets: [
    .target(name: "JobPursuit", path: "Sources"),
    .testTarget(name: "NativeHostTests", dependencies: ["JobPursuit"], path: "Tests")
])
''')
    code, output = run(["swift", "test", "--package-path", str(artifacts), "--jobs", "2"],
                       artifacts, "host-tests", timeout=150, check=False)
    cases = dict(re.findall(r"Test Case '([^']+)' (passed|failed|skipped)", output))
    counts = {key: sum(value == key for value in cases.values()) for key in ("passed", "failed", "skipped")}
    counts.update(expected=expected_count(["JobPursuitTests"]), exitCode=code,
                  platform="macOS ARM64 host; not iOS/UI", cases=cases)
    (artifacts / "case-counts.json").write_text(json.dumps(counts, indent=2) + "\n")
    print(output[-16000:], flush=True)
    print(json.dumps(counts), flush=True)
    return 0 if code == 0 and counts["failed"] == 0 and counts["skipped"] == 1 and counts["passed"] + 1 == counts["expected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
