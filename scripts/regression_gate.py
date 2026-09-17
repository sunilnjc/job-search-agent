#!/usr/bin/env python3
"""Serial, offline Python/SQL release gate; no DSN, credentials or installs.

Run with the project's installed Python dependencies and native PostgreSQL:
  .venv/bin/python -B scripts/regression_gate.py --summary /tmp/new-result.json

Never treats missing SQL, skipped tests or expected failures as a green gate.
Linux CI runs this same entry point. This is real local PostgreSQL SQL coverage,
not hosted Supabase, live-provider, browser or native-device acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SQL_SUITES = (
    "test_discovery_preferences.DiscoveryRulesPostgresTests",
    "test_mobile_billing_sql.BillingPostgresTests",
    "test_mobile_privacy_sql.PrivacyPostgresTests",
    "test_mobile_privacy_lifecycle_sql.PrivacyLifecyclePostgresTests",
    "test_mobile_profile_sql.AtomicProfilePostgresTests",
    "test_mobile_readiness_sql.ReadinessPostgresTests",
    "test_gtm_operator_metrics_sql.OperatorMetricsPostgresTests",
)


def safe_environment(environ):
    """Only runtime paths survive; provider/service keys and proxies do not."""
    result = {
        "PATH": str(Path(sys.executable).parent) + ":/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT / "tests"),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
    }
    for name in ("JOBPURSUIT_POSTGRES_BIN", "JOBPURSUIT_PSQL"):
        if environ.get(name):
            result[name] = environ[name]
    return result


def offline_audit(event, args):
    """Guard actual Python network and real workspace-private file access.

    Mocks and ASGI transports still execute the product logic. AF_UNIX is
    allowed for event-loop self-pipes. SQL clients use only our private sockets.
    This is a safety guard, not an OS sandbox for arbitrary child programs.
    """
    if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        raise PermissionError("Regression gate forbids live DNS")
    if event in {"socket.connect", "socket.sendto", "socket.bind"} and args:
        if getattr(args[0], "family", None) in (socket.AF_INET, socket.AF_INET6):
            raise PermissionError("Regression gate forbids live IP networking")
    if event not in {"open", "sqlite3.connect"} or not args or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    path = Path(os.fsdecode(args[0])).resolve()
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return  # Synthetic fixtures created outside the workspace are allowed.
    private = {".git", ".openai", "config", "resumes", "output", "logs", "playwright"}
    if ((relative.parts and relative.parts[0] in private) or path.name == ".env" or
            path.name.startswith(".env.") and not path.name.endswith(".example") or
            path.suffix in {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".p8"}):
        raise PermissionError("Regression gate forbids real workspace private data")


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


class GateResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        self.deadline = kwargs.pop('deadline', None)
        super().__init__(*args, **kwargs)
        self.passed_ids = []

    def startTest(self, test):
        if self.deadline:
            self.deadline.check()
        super().startTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_ids.append(test.id())


class GateDeadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds
        self.cancelled = False

    def check(self):
        if self.cancelled or time.monotonic() >= self.end:
            raise KeyboardInterrupt('Regression deadline or supervisor cancellation')

    def interrupt(self, signum, frame):
        # unittest's import loader catches BaseException (including an alarm
        # during imports). Remember cancellation so it cannot disappear into
        # an import error and let the rest of the suite continue or turn green.
        self.cancelled = True
        raise KeyboardInterrupt('Regression deadline or supervisor cancellation')


def summarize(result, discovered, elapsed):
    expected = Counter(name.rsplit('.', 1)[0] for name in discovered)
    passed = Counter(name.rsplit('.', 1)[0] for name in result.passed_ids)
    # Count the actual SQL classes, not transport/unit tests that happen to be
    # defined alongside them. Discovery preferences also has its own SQL class.
    sql = {suite: {"discovered": expected[suite], "passed": passed[suite]}
           for suite in SQL_SUITES}
    complete = (result.wasSuccessful() and not result.skipped and not result.expectedFailures and
                not result.unexpectedSuccesses and result.testsRun == len(discovered) and
                Counter(result.passed_ids) == Counter(discovered) and bool(discovered) and
                all(row["discovered"] > 0 and row["passed"] == row["discovered"] for row in sql.values()))
    return {"gate": "PASS" if complete else "FAIL", "scope": "offline Python + disposable PostgreSQL",
            "discovered": len(discovered), "executed": result.testsRun,
            "passed": len(result.passed_ids), "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses), "sql_suites": sql,
            "elapsed_seconds": round(elapsed, 3),
            "failed_tests": [test.id() for test, _ in result.failures + result.errors],
            "skipped_tests": [test.id() for test, _ in result.skipped]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, help="New JSON evidence file; existing files are never overwritten")
    parser.add_argument("--timeout", type=int, default=600, help="Python test deadline in seconds (1..1200), followed by owned cleanup")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 1200:
        parser.error("timeout must be between 1 and 1200 seconds")
    # Reserve the exact output before running, so evidence cannot overwrite
    # other work. No default evidence path or automatic git operations.
    output = args.summary.open("x", encoding="utf-8") if args.summary else None
    environment = safe_environment(os.environ)
    os.environ.clear()
    os.environ.update(environment)
    os.chdir(ROOT)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
    sys.addaudithook(offline_audit)
    from local_postgres import cleanup_owned_clusters

    deadline = GateDeadline(args.timeout)
    signal.signal(signal.SIGTERM, deadline.interrupt)
    signal.signal(signal.SIGALRM, deadline.interrupt)
    signal.alarm(args.timeout)
    started = time.monotonic()
    summary = {"gate": "FAIL", "reason": "interrupted_or_infrastructure_failure"}
    try:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        deadline.check()
        discovered = list(test_ids(suite))
        factory = lambda *args, **kwargs: GateResult(*args, deadline=deadline, **kwargs)
        result = unittest.TextTestRunner(verbosity=2, resultclass=factory).run(suite)
        deadline.check()
        summary = summarize(result, discovered, time.monotonic() - started)
    except KeyboardInterrupt:
        pass
    finally:
        signal.alarm(0)
        cleanup_owned_clusters()
        # Cleanup failures must not produce a green gate either.
        from local_postgres import _ACTIVE_CLUSTERS
        if _ACTIVE_CLUSTERS:
            summary.update(gate="FAIL", cleanup_incomplete=True)
        encoded = json.dumps(summary, indent=2) + "\n"
        if output:
            output.write(encoded)
            output.close()
        print(encoded, flush=True)
    return 0 if summary["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
