"""Gate meta-tests: false greens, private-data guards and CI resource bounds."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import socket
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('regression_gate', ROOT / 'scripts/regression_gate.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class RegressionGateTests(unittest.TestCase):
    def green(self):
        ids = [name + '.test_real_sql' for name in gate.SQL_SUITES] + ['test_unit.Unit.test_one']
        result = SimpleNamespace(passed_ids=list(ids), testsRun=len(ids), failures=[], errors=[],
                                 skipped=[], expectedFailures=[], unexpectedSuccesses=[], wasSuccessful=lambda: True)
        return result, ids

    def test_exact_full_success_is_green_with_sql_counts(self):
        result, ids = self.green()
        summary = gate.summarize(result, ids, 1.25)
        self.assertEqual(summary['gate'], 'PASS')
        self.assertEqual(summary['passed'], summary['discovered'])
        self.assertTrue(all(row == {'passed': 1, 'discovered': 1} for row in summary['sql_suites'].values()))

    def test_deadline_expiry_and_swallowed_import_interrupt_stay_cancelled(self):
        with patch.object(gate.time, 'monotonic', return_value=10) as clock:
            deadline = gate.GateDeadline(1)
            deadline.check()
            clock.return_value = 11
            with self.assertRaises(KeyboardInterrupt):
                deadline.check()
        deadline = gate.GateDeadline(600)
        try:
            deadline.interrupt(None, None)
        except KeyboardInterrupt:
            pass  # Reproduce unittest discovery swallowing the first signal.
        with self.assertRaises(KeyboardInterrupt):
            deadline.check()

    def test_cancelled_deadline_stops_before_next_test_is_counted(self):
        import io
        deadline = gate.GateDeadline(600)
        deadline.cancelled = True
        result = gate.GateResult(io.StringIO(), True, 0, deadline=deadline)
        with self.assertRaises(KeyboardInterrupt):
            result.startTest(SimpleNamespace(id=lambda: 'synthetic'))
        self.assertEqual(result.testsRun, 0)

    def test_missing_sql_and_partial_suite_fail_even_if_unittest_successful(self):
        for missing in gate.SQL_SUITES:
            result, ids = self.green()
            ids = [name for name in ids if not name.startswith(missing + '.')]
            result.passed_ids = ids
            self.assertEqual(gate.summarize(result, ids, 1)['gate'], 'FAIL')
        result, ids = self.green()
        result.passed_ids.pop()
        self.assertEqual(gate.summarize(result, ids, 1)['gate'], 'FAIL')
        self.assertEqual(gate.summarize(result, [], 1)['gate'], 'FAIL')

    def test_non_sql_tests_in_same_module_do_not_replace_missing_sql_class(self):
        result, ids = self.green()
        sql_class = 'test_mobile_privacy_lifecycle_sql.PrivacyLifecyclePostgresTests'
        ids = [name for name in ids if not name.startswith(sql_class + '.')]
        ids.append('test_mobile_privacy_lifecycle_sql.PrivacyLifecycleWorkerAcceptance.test_fake_transport')
        result.passed_ids = ids
        summary = gate.summarize(result, ids, 1)
        self.assertEqual(summary['gate'], 'FAIL')
        self.assertEqual(summary['sql_suites'][sql_class], {'discovered': 0, 'passed': 0})

    def test_skips_expected_failures_and_setup_errors_are_not_green(self):
        for field in ('skipped', 'expectedFailures', 'errors', 'failures'):
            result, ids = self.green()
            setattr(result, field, [(SimpleNamespace(id=lambda: 'synthetic_test'), 'safe synthetic reason')])
            if field in ('errors', 'failures'):
                result.wasSuccessful = lambda: False
            self.assertEqual(gate.summarize(result, ids, 1)['gate'], 'FAIL')

    def test_wrong_test_identity_or_run_count_cannot_replace_coverage(self):
        result, ids = self.green()
        result.passed_ids[-1] = 'test_other.Unit.test_one'
        self.assertEqual(gate.summarize(result, ids, 1)['gate'], 'FAIL')
        result, ids = self.green()
        result.testsRun = 0
        self.assertEqual(gate.summarize(result, ids, 1)['gate'], 'FAIL')

    def test_environment_has_only_explicit_runtime_paths_no_secrets_or_proxies(self):
        environment = gate.safe_environment({'OPENAI_API_KEY': 'synthetic', 'SUPABASE_SECRET_KEY': 'synthetic',
            'GH_TOKEN': 'synthetic', 'HTTP_PROXY': 'synthetic', 'PGHOST': 'not-a-test-db',
            'PGPASSWORD': 'synthetic', 'HOME': '/synthetic', 'PATH': '/untrusted',
            'JOBPURSUIT_POSTGRES_BIN': '/synthetic/pg/bin', 'JOBPURSUIT_PSQL': '/synthetic/psql'})
        self.assertEqual(environment['JOBPURSUIT_POSTGRES_BIN'], '/synthetic/pg/bin')
        self.assertEqual(environment['JOBPURSUIT_PSQL'], '/synthetic/psql')
        for name in ('OPENAI_API_KEY', 'SUPABASE_SECRET_KEY', 'GH_TOKEN', 'HTTP_PROXY', 'PGHOST', 'PGPASSWORD', 'HOME'):
            self.assertNotIn(name, environment)
        self.assertEqual(environment['PYTHON_DOTENV_DISABLED'], '1')
        self.assertEqual(environment['GIT_CONFIG_GLOBAL'], os.devnull)

    def test_real_private_paths_blocked_but_source_and_synthetic_fixtures_allowed(self):
        for path in ('.env.privacy', '.env', 'src/.env', 'web/.env.local', 'config/preferences.yaml',
                     'resumes/source.pdf', 'output/a.docx', '.git/config', 'jobagent.db', 'private.key'):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                gate.offline_audit('open', (ROOT / path, 'r'))
        for path in (ROOT / 'src/jobagent/mobile/app.py', ROOT / 'tests/fixtures/resumes/source.pdf',
                     ROOT / '.env.example', Path('/tmp/synthetic-test/.env')):
            gate.offline_audit('open', (path, 'r'))

    def test_live_network_is_denied_but_unix_socket_self_pipe_allowed(self):
        for family in (socket.AF_INET, socket.AF_INET6):
            for event in ('socket.connect', 'socket.bind', 'socket.sendto'):
                with self.assertRaises(PermissionError):
                    gate.offline_audit(event, (SimpleNamespace(family=family),))
        with self.assertRaises(PermissionError):
            gate.offline_audit('socket.getaddrinfo', ('synthetic.invalid',))
        gate.offline_audit('socket.connect', (SimpleNamespace(family=socket.AF_UNIX),))

    def test_workflow_is_manual_serial_no_application_credentials(self):
        import yaml
        # BaseLoader avoids YAML 1.1 interpreting the GitHub key `on` as bool.
        config = yaml.load((ROOT / '.github/workflows/regression-gate.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(set(config['on']), {'workflow_dispatch'})
        self.assertEqual(config['permissions'], {'contents': 'read'})
        self.assertEqual(len(config['jobs']), 1)
        job = config['jobs']['regression']
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertNotIn('strategy', job)
        self.assertNotIn('services', job)  # Each class has a fresh private Unix-socket cluster instead.
        source = (ROOT / '.github/workflows/regression-gate.yml').read_text()
        self.assertNotRegex(source, r'\$\{\{[^}]*secrets[.\[]')
        self.assertIn('persist-credentials: false', source)
        self.assertIn('scripts/regression_gate.py', source)
        self.assertIn('--test-concurrency=1', source)
        self.assertNotIn('actions/upload-artifact', source)
        self.assertNotIn('actions/cache', source)
        self.assertIn('GITHUB_STEP_SUMMARY', source)


if __name__ == '__main__':
    unittest.main()
