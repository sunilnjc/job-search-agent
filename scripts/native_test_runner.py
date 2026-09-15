#!/usr/bin/env python3
"""Bounded, offline XCTest runner; never erase or delete existing simulators.

Example (reuse already built products, without modifying their xctestrun):
  python3 scripts/native_test_runner.py --xctestrun PATH --only-testing JobPursuitTests

Without --xctestrun, generate a project outside the checkout and build there.
Each xcodebuild gets at most 190 seconds plus 42 seconds of cancellation grace.
Artifacts are retained in the printed temporary directory. Select one existing
simulator explicitly. --reuse-booted preserves an already-booted simulator's
state; otherwise only a simulator booted by this run is shut down afterward.
This runner never creates, erases or deletes simulators or uninstalls apps.
No signing settings are changed. Apple tools run natively on Apple Silicon.
"""

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import tempfile
import time
import uuid
from native_device_test_runner import native_command, stop_owned_group


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "ios/JobPursuit"


def stop(process):
    # Popen starts a new session: never signal another task's process group.
    # Xcode needs up to 30 seconds to finalize xcresult after cancellation.
    stop_owned_group(process)


def run(command, artifact_dir, label, timeout=60, check=True):
    log = artifact_dir / (label + ".log")
    print(f"{label}: timeout={timeout}s; log={log}", flush=True)
    started = time.monotonic()
    with log.open("w") as output:
        process = subprocess.Popen(native_command(command), stdout=output, stderr=subprocess.STDOUT,
                                   start_new_session=True, cwd=ROOT)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop(process)
            code = 124
            print(f"{label}: TIMED OUT; stopped own subprocess group", flush=True)
        except BaseException:
            stop(process)
            raise
    print(f"{label}: exit={code}; elapsed={time.monotonic() - started:.1f}s", flush=True)
    content = log.read_text(errors="replace")
    if check and code:
        print(content[-6000:], flush=True)
        raise RuntimeError(f"{label} failed ({code}); see {log}")
    return code, content


def offline_xctestrun(source, destination):
    # Generated artifact only: preserve the original products and build manifest.
    with source.open("rb") as stream:
        spec = plistlib.load(stream)

    def paths(value):
        if isinstance(value, str):
            return value.replace("__TESTROOT__", str(source.parent))
        if isinstance(value, dict):
            return {key: paths(item) for key, item in value.items()}
        if isinstance(value, list):
            return [paths(item) for item in value]
        return value

    spec = paths(spec)
    if spec.get("__xctestrun_metadata__", {}).get("FormatVersion") != 1:
        raise RuntimeError("Expected scheme-generated xctestrun format 1")
    for name, target in spec.items():
        if name.startswith("__"):
            continue
        expected = {"JobPursuitTests": "com.thejobpursuit.ios",
                    "JobPursuitUITests": "com.thejobpursuit.JobPursuitUITests.xctrunner"}
        if name not in expected or target.get("TestHostBundleIdentifier") != expected[name]:
            raise RuntimeError("Refusing a non-Job-Pursuit test host")
        for key in ("TestHostPath", "UITargetAppPath"):
            if key not in target:
                continue
            info = plistlib.loads((Path(target[key]) / "Info.plist").read_bytes())
            identifier = expected[name] if key == "TestHostPath" else "com.thejobpursuit.ios"
            if info.get("CFBundleIdentifier") != identifier:
                raise RuntimeError("Built product identity does not match the Job Pursuit manifest")
        args = target.setdefault("CommandLineArguments", [])
        if "--uitesting" not in args:
            args.append("--uitesting")
        target["TestTimeoutsEnabled"] = True
        target["DefaultTestExecutionTimeAllowance"] = 60
        target["MaximumTestExecutionTimeAllowance"] = 90
    with destination.open("wb") as stream:
        plistlib.dump(spec, stream)


def case_counts(tree):
    """xcresult's totalTestCount includes synthetic 'System Failures' cases."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "other": 0, "systemFailures": 0}

    def visit(node, system=False):
        system = system or node.get("name") == "System Failures"
        if node.get("nodeType") == "Test Case":
            if system:
                counts["systemFailures"] += 1
            else:
                status = node.get("result", "").lower()
                counts[status if status in {"passed", "failed", "skipped"} else "other"] += 1
        for child in node.get("children", []):
            visit(child, system)

    for node in tree.get("testNodes", []):
        visit(node)
    counts["executed"] = counts["passed"] + counts["failed"]
    return counts


def expected_count(selectors):
    selected = 0
    for folder, target in [("Tests", "JobPursuitTests"), ("UITests", "JobPursuitUITests")]:
        for source in (PROJECT / folder).glob("*.swift"):
            text = source.read_text()
            case_class = re.search(r"class\s+(\w+)\s*:\s*XCTestCase", text)
            if not case_class:
                continue
            for method in re.findall(r"func\s+(test\w+)\s*\(", text):
                identifier = f"{target}/{case_class[1]}/{method}"
                if not selectors or any(identifier == s.removesuffix("()") or
                                        identifier.startswith(s.rstrip("/") + "/") for s in selectors):
                    selected += 1
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xctestrun", type=Path)
    parser.add_argument("--device", help="Exact UUID of one existing simulator; no device is created")
    parser.add_argument("--expected-device-name", help="Refuse a selected simulator whose name differs; keep machine-specific IDs outside source control")
    parser.add_argument("--reuse-booted", action="store_true", help="Use and leave running the exact already-booted simulator")
    parser.add_argument("--runtime", default="com.apple.CoreSimulator.SimRuntime.iOS-26-5")
    parser.add_argument("--device-type", default="com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--only-testing", action="append", default=[])
    args = parser.parse_args()
    if not args.build_only and not args.device:
        parser.error("Select an existing --device; this runner does not create or delete simulators")
    if args.device:
        args.device = str(uuid.UUID(args.device)).upper()
    artifacts = Path(tempfile.mkdtemp(prefix="job-pursuit-native-tests-"))
    print(f"Artifacts: {artifacts}", flush=True)
    simulator = None
    booted_here = False
    try:
        source = args.xctestrun.resolve() if args.xctestrun else None
        if source is None:
            # The app's INFOPLIST_FILE is relative to PROJECT_DIR. Keep its
            # existing value and config files readable from the external project.
            (artifacts / "Config").symlink_to(PROJECT / "Config", target_is_directory=True)
            run(["xcodegen", "generate", "--spec", str(PROJECT / "project.yml"),
                 "--project", str(artifacts), "--project-root", str(PROJECT)],
                artifacts, "generate")
            run(["xcodebuild", "build-for-testing", "-project", str(artifacts / "JobPursuit.xcodeproj"),
                 "-scheme", "JobPursuit", "-destination", "generic/platform=iOS Simulator",
                 "-derivedDataPath", str(artifacts / "DerivedData"), "-jobs", "2",
                 "ARCHS=arm64", "ONLY_ACTIVE_ARCH=YES"],
                artifacts, "build", timeout=190)
            sources = list((artifacts / "DerivedData/Build/Products").glob("*.xctestrun"))
            if len(sources) != 1:
                raise RuntimeError(f"Expected one xctestrun; found {sources}")
            source = sources[0]
        offline_xctestrun(source, artifacts / "Offline.xctestrun")
        if args.build_only:
            print(f"Built test manifest: {source}", flush=True)
            return 0
        if args.device:
            _, raw = run(["xcrun", "simctl", "list", "devices", "available", "--json"], artifacts, "devices-before")
            devices = [item for group in json.loads(raw)["devices"].values() for item in group]
            device = next(item for item in devices if item["udid"] == args.device)
            if args.expected_device_name and device["name"] != args.expected_device_name:
                raise RuntimeError("Refusing to operate on a renamed QA simulator")
            if device["state"] != ("Booted" if args.reuse_booted else "Shutdown"):
                raise RuntimeError("Simulator state does not match the explicit reuse/boot request")
            simulator = args.device
        print(f"Simulator: {simulator}; preserve_booted={args.reuse_booted}", flush=True)
        if not args.reuse_booted:
            run(["xcrun", "simctl", "boot", simulator], artifacts, "boot")
            booted_here = True
        run(["xcrun", "simctl", "bootstatus", simulator, "-b"], artifacts, "bootstatus", timeout=60)
        result = artifacts / "Tests.xcresult"
        command = ["xcodebuild", "test-without-building", "-xctestrun", str(artifacts / "Offline.xctestrun"),
                   "-destination", "platform=iOS Simulator,id=" + simulator, "-destination-timeout", "30",
                   "-resultBundlePath", str(result), "-parallel-testing-enabled", "NO",
                   "-maximum-concurrent-test-simulator-destinations", "1",
                   "-test-timeouts-enabled", "YES", "-default-test-execution-time-allowance", "60",
                   "-maximum-test-execution-time-allowance", "90", "-collect-test-diagnostics", "never"]
        command += ["-only-testing:" + target for target in args.only_testing]
        code, output = run(command, artifacts, "test", timeout=190, check=False)
        print(output[-7000:], flush=True)
        summary_code, summary = run(["xcrun", "xcresulttool", "get", "test-results", "summary", "--path", str(result)],
                                    artifacts, "summary", check=False)
        print(summary, flush=True)
        cases_code, cases = run(["xcrun", "xcresulttool", "get", "test-results", "tests", "--path", str(result)],
                               artifacts, "test-cases", check=False)
        counts = case_counts(json.loads(cases)) if cases_code == 0 else {}
        counts["expectedFromCurrentSource"] = expected_count(args.only_testing)
        counts["xcresult"] = str(result)
        (artifacts / "case-counts.json").write_text(json.dumps(counts, indent=2) + "\n")
        print("Actual XCTest cases: " + json.dumps(counts), flush=True)
        if code or summary_code or cases_code:
            run(["xcrun", "xcresulttool", "export", "diagnostics", "--path", str(result),
                 "--output-path", str(artifacts / "diagnostics")], artifacts, "export-diagnostics", check=False)
            return 1
        return 0 if (counts.get("passed", 0) > 0 and counts.get("failed") == 0 and
                     counts.get("systemFailures") == 0 and counts.get("other") == 0 and
                     counts["passed"] == counts["expectedFromCurrentSource"]) else 1
    finally:
        if simulator and booted_here:
            run(["xcrun", "simctl", "shutdown", simulator], artifacts, "shutdown", check=False)
        print(f"Retained artifacts: {artifacts}", flush=True)


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        raise SystemExit(main())
    except (RuntimeError, KeyboardInterrupt) as error:
        print(str(error) or "Interrupted; own subprocess/simulator cleaned up", flush=True)
        raise SystemExit(1)
