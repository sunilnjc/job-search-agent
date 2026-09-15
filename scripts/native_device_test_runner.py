#!/usr/bin/env python3
"""Bounded command wrapper for a manually audited physical-device XCTest run.

This does not authorize, install, launch, unlock, or clean up a phone itself.
The caller must audit source isolation and lock state before supplying a command.
Use one fresh external artifact directory and distinct generate/build/unit/ui labels.
Each label is single-use. Provisioning updates require explicit operator approval
and the opt-in flag, and are accepted only for build-for-testing. Signing identity
overrides and automatic device registration remain forbidden.
Raw output stays in mode-0600 local logs; console output contains status only.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


MAX_RUNTIME = 180
GRACES = ((signal.SIGINT, 30), (signal.SIGTERM, 5), (signal.SIGKILL, 2))
ROOT = Path(__file__).resolve().parents[1]


def native_command(command):
    """An Intel Python under Rosetta must not choose an Intel XCTest launcher."""
    # xcodegen may be Intel-only; it writes project files but launches no tests.
    if sys.platform == "darwin" and Path(command[0]).name in {"xcodebuild", "xcrun", "swift", "swiftc"}:
        supported = subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], timeout=5).strip()
        if supported == b"1":
            return ["/usr/bin/arch", "-arm64", *command]
    return command


def validate_command(command, allow_provisioning_updates=False):
    if not command:
        raise ValueError("Missing command")
    forbidden = ("-allowProvisioning", "DEVELOPMENT_TEAM=", "CODE_SIGN",
                 "PROVISIONING_PROFILE", "PRODUCT_BUNDLE_IDENTIFIER=")
    provisioning_allowed = (allow_provisioning_updates
                            and Path(command[0]).name == "xcodebuild"
                            and "build-for-testing" in command
                            and "test-without-building" not in command)
    if any(arg.startswith(forbidden)
           and not (provisioning_allowed and arg == "-allowProvisioningUpdates")
           for arg in command):
        raise ValueError("Provisioning updates and signing/identity overrides are forbidden")
    if Path(command[0]).name not in {"xcodebuild", "xcodegen", "xcrun"}:
        raise ValueError("Only Xcode generation, build and result tools are accepted")
    if Path(command[0]).name == "xcrun" and command[1:2] != ["xcresulttool"]:
        raise ValueError("Device mutations are not supported by this wrapper")
    if Path(command[0]).name == "xcodebuild":
        if not any(action in command for action in ("build-for-testing", "test-without-building")):
            raise ValueError("Only explicit build/test actions are supported")
        if any(arg in command for arg in ("-retry-tests-on-failure", "-run-tests-until-failure", "-test-iterations")):
            raise ValueError("Test retries are forbidden")


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_owned_group(process):
    """Signal only our start_new_session process group, including orphaned children."""
    for sig, grace in GRACES:
        process.poll()  # Reap the leader even when descendants remain.
        if not group_alive(process.pid):
            return True
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            process.poll()
            if not group_alive(process.pid):
                return True
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    process.poll()
    return not group_alive(process.pid)


def bounded_run(command, artifacts, label, timeout, allow_provisioning_updates=False):
    validate_command(command, allow_provisioning_updates=allow_provisioning_updates)
    if not 0 < timeout <= MAX_RUNTIME:
        raise ValueError("Runtime must be positive and at most 180 seconds")
    if not label or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in label):
        raise ValueError("Invalid artifact label")
    artifacts = artifacts.resolve(strict=True)
    temporary_root = Path(tempfile.gettempdir()).resolve()
    # macOS /tmp and tempfile.gettempdir() can refer to different temporary roots.
    if not any(parent in artifacts.parents for parent in (temporary_root, Path("/tmp").resolve())):
        raise ValueError("Artifacts must be in a separate temporary directory")
    log = artifacts / (label + ".log")
    started = time.monotonic()
    # Exclusive creation also makes each build/unit/UI label a one-attempt latch.
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    process = None
    timed_out = False
    interrupted = False
    cleaned = True
    code = None
    try:
        with os.fdopen(descriptor, "w") as output:
            process = subprocess.Popen(native_command(command), stdout=output, stderr=subprocess.STDOUT,
                                       cwd=ROOT, start_new_session=True)
            print(f"{label}: pid={process.pid}; limit={timeout}+37s; log={log}", flush=True)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                code = 124
            except KeyboardInterrupt:
                interrupted = True
                code = 130
            finally:
                cleaned = stop_owned_group(process)
    finally:
        status = {"label": label, "exitCode": code, "timedOut": timed_out,
                  "interrupted": interrupted, "ownProcessGroupCleaned": cleaned,
                  "elapsedSeconds": round(time.monotonic() - started, 3),
                  "runtimeLimitSeconds": timeout, "cleanupLimitSeconds": 37,
                  "provisioningUpdatesRequested": "-allowProvisioningUpdates" in command,
                  "pid": process.pid if process else None, "log": str(log)}
        with (artifacts / (label + "-status.json")).open("x") as output:
            json.dump(status, output, indent=2)
            output.write("\n")
        print(json.dumps(status), flush=True)
    return code if code else (0 if cleaned else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--timeout", type=float, default=170)
    parser.add_argument("--allow-provisioning-updates", action="store_true",
                        help="Use only after explicit approval to provision with the existing Apple account")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return bounded_run(command, args.artifacts, args.label, args.timeout,
                       allow_provisioning_updates=args.allow_provisioning_updates)


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(f"Runner stopped: {type(error).__name__}", flush=True)
        raise SystemExit(1)
