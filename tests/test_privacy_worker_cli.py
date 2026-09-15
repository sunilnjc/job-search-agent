"""Privacy CLI configuration tests: synthetic files, no live worker/network."""
import asyncio
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/privacy_worker.py'
spec = importlib.util.spec_from_file_location('privacy_worker_cli', SCRIPT)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

URL_KEY = 'MOBILE_PRIVACY_SUPABASE_URL'
SECRET_KEY = 'MOBILE_PRIVACY_SUPABASE_SECRET_KEY'
SECRET = 'sb_secret_SYNTHETIC_FILE_SENTINEL'
ENV_TEXT = f'{URL_KEY}=https://privacy.invalid\n{SECRET_KEY}={SECRET}\n'
REQUEST = UUID('33333333-3333-4333-8333-333333333333')


class PrivacyWorkerCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jp-privacy-cli-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env_file = self.root / 'synthetic.env'
        self.env_file.write_text(ENV_TEXT, encoding='utf-8')
        self.env_file.chmod(0o600)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        for name in ('connect', 'connect_ex'):
            self.stack.enter_context(patch.object(socket.socket, name, side_effect=AssertionError('No test network')))
        self.stack.enter_context(patch.object(socket, 'getaddrinfo', side_effect=AssertionError('No test DNS')))

    def invoke(self, args=(), worker=None):
        factory = Mock(return_value=worker) if worker else Mock(side_effect=AssertionError('Dry run constructed worker'))
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, 'PrivacyWorker', factory), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = asyncio.run(cli.main(list(args)))
        transcript = output.getvalue() + errors.getvalue()
        self.assertNotIn('SYNTHETIC_FILE_SENTINEL', transcript)
        self.assertNotIn('SYNTHETIC_EXISTING_SENTINEL', transcript)
        return status, transcript, factory

    def test_explicit_file_loads_quoted_export_assignments_and_is_dry_run(self):
        self.env_file.write_text('\ufeff# private fixture\nexport ' + URL_KEY + '="https://privacy.invalid"\n'
                                 + SECRET_KEY + "='" + SECRET + "'\n", encoding='utf-8')
        code, output, factory = self.invoke(['--env-file', str(self.env_file)])
        self.assertEqual(code, 0)
        self.assertIn('Dry run: no network, export, deletion, or heartbeat performed.', output)
        self.assertEqual(os.environ[URL_KEY], 'https://privacy.invalid')
        self.assertEqual(os.environ[SECRET_KEY], SECRET)
        factory.assert_not_called()

    def test_existing_environment_wins_even_when_empty(self):
        os.environ.update({URL_KEY: 'https://supervisor.invalid', SECRET_KEY: 'sb_secret_SYNTHETIC_EXISTING_SENTINEL'})
        code, _, _ = self.invoke(['--env-file', str(self.env_file)])
        self.assertEqual(code, 0)
        self.assertEqual(os.environ[URL_KEY], 'https://supervisor.invalid')
        self.assertEqual(os.environ[SECRET_KEY], 'sb_secret_SYNTHETIC_EXISTING_SENTINEL')
        os.environ[SECRET_KEY] = ''
        code, output, factory = self.invoke(['--env-file', str(self.env_file)])
        self.assertEqual(code, 2)
        self.assertIn('configuration invalid', output)
        self.assertEqual(os.environ[SECRET_KEY], '')
        factory.assert_not_called()

    def test_no_implicit_dotenv_discovery(self):
        # A subprocess also verifies __main__ execution with no imported wrapper.
        for name in ('.env', '.env.privacy'):
            (self.root / name).write_text(ENV_TEXT, encoding='utf-8')
        result = subprocess.run([sys.executable, '-B', str(SCRIPT)], cwd=self.root,
            env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'},
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('configuration invalid', result.stdout)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        with patch.object(cli, 'load_env_file', side_effect=AssertionError('No default file')) as loader:
            code, _, factory = self.invoke()
        self.assertEqual(code, 2)
        loader.assert_not_called()
        factory.assert_not_called()

    def test_default_environment_only_dry_run_keeps_compatibility(self):
        os.environ.update({URL_KEY: 'https://existing.invalid', SECRET_KEY: SECRET})
        code, output, factory = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn('Dry run', output)
        factory.assert_not_called()

    def test_cli_explicit_file_from_different_working_directory(self):
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--env-file', str(self.env_file)], cwd=self.root,
            env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'},
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Dry run', result.stdout)
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_file_errors_are_sanitized_and_do_not_construct_worker(self):
        os.environ.update({URL_KEY: 'https://existing.invalid', SECRET_KEY: SECRET})
        for path in (self.root / 'SYNTHETIC_FILE_SENTINEL_missing', self.root):
            with self.subTest(kind='missing' if path != self.root else 'directory'):
                code, output, factory = self.invoke(['--env-file', str(path), '--execute'])
                self.assertEqual(code, 2)
                self.assertIn('Unable to load env file', output)
                factory.assert_not_called()
        with patch.object(cli.os, 'open', side_effect=PermissionError('SYNTHETIC_FILE_SENTINEL')):
            self.assertEqual(self.invoke(['--env-file', str(self.env_file)])[0], 2)

    def test_fifo_is_rejected_without_blocking(self):
        fifo = self.root / 'fixture.fifo'
        os.mkfifo(fifo, 0o600)
        code, _, factory = self.invoke(['--env-file', str(fifo)])
        self.assertEqual(code, 2)
        factory.assert_not_called()

    def test_malformed_or_oversized_file_has_no_partial_environment_write(self):
        samples = [
            ENV_TEXT + "BROKEN='SYNTHETIC_FILE_SENTINEL\n",
            ENV_TEXT + 'BARE_KEY\n',
            ENV_TEXT + 'INVALID.KEY=value\n',
            ENV_TEXT + 'NUL=value\x00suffix\n',
            ENV_TEXT + 'EXCESS=' + 'x' * cli.MAX_ENV_FILE_BYTES,
        ]
        for value in samples:
            with self.subTest(size=len(value)):
                self.env_file.write_text(value, encoding='utf-8')
                code, output, factory = self.invoke(['--env-file', str(self.env_file)])
                self.assertEqual(code, 2)
                self.assertEqual(dict(os.environ), {})
                self.assertIn('Unable to load env file', output)
                factory.assert_not_called()
        self.env_file.write_bytes(b'KEY=\xff\xfe')
        self.assertEqual(self.invoke(['--env-file', str(self.env_file)])[0], 2)
        self.assertEqual(dict(os.environ), {})

    def test_values_are_literal_not_interpolated_or_shell_evaluated(self):
        os.environ['EXISTING'] = 'SYNTHETIC_EXISTING_SENTINEL'
        self.env_file.write_text(ENV_TEXT + 'LITERAL="${EXISTING} $(not-a-command)"\n', encoding='utf-8')
        code, _, _ = self.invoke(['--env-file', str(self.env_file)])
        self.assertEqual(code, 0)
        self.assertEqual(os.environ['LITERAL'], '${EXISTING} $(not-a-command)')

    def test_settings_parse_errors_never_echo_bad_url_or_secret(self):
        for origin in ('https://privacy.invalid:SYNTHETIC_FILE_SENTINEL', 'https://[SYNTHETIC_FILE_SENTINEL'):
            os.environ.update({URL_KEY: origin, SECRET_KEY: SECRET})
            code, output, factory = self.invoke()
            self.assertEqual(code, 2)
            self.assertIn('configuration invalid', output)
            factory.assert_not_called()

    def test_request_id_alone_does_not_authorize_execution(self):
        code, _, factory = self.invoke(['--env-file', str(self.env_file), '--request-id', str(REQUEST)])
        self.assertEqual(code, 0)
        factory.assert_not_called()

    def test_execute_invokes_run_once_exactly_once_and_closes(self):
        worker = SimpleNamespace(run_once=AsyncMock(return_value={'state': 'idle'}), close=AsyncMock())
        code, output, factory = self.invoke(['--env-file', str(self.env_file), '--execute', '--request-id', str(REQUEST)], worker)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), {'state': 'idle'})
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args[0].url, 'https://privacy.invalid')
        self.assertEqual(factory.call_args.args[0].secret, SECRET)
        worker.run_once.assert_awaited_once_with(request_id=REQUEST)
        worker.close.assert_awaited_once()

    def test_complete_result_exits_zero_without_extra_output_or_retry(self):
        result = {'state': 'complete', 'request_id': str(REQUEST), 'kind': 'export'}
        worker = SimpleNamespace(run_once=AsyncMock(return_value=result), close=AsyncMock())
        code, output, factory = self.invoke(['--env-file', str(self.env_file), '--execute'], worker)
        self.assertEqual(code, 0)
        self.assertEqual(output, json.dumps(result) + '\n')
        factory.assert_called_once()
        worker.run_once.assert_awaited_once_with(request_id=None)
        worker.close.assert_awaited_once()

    def test_blocked_result_exits_one_with_only_safe_json_and_no_retry(self):
        result = {'state': 'blocked', 'request_id': str(REQUEST), 'code': 'billing_blocked'}
        for request_id in (None, REQUEST):
            with self.subTest(explicit_resume=request_id is not None):
                worker = SimpleNamespace(run_once=AsyncMock(return_value=result), close=AsyncMock())
                args = ['--env-file', str(self.env_file), '--execute']
                if request_id is not None:
                    args.extend(['--request-id', str(request_id)])
                code, output, factory = self.invoke(args, worker)
                self.assertEqual(code, 1)
                self.assertEqual(output, json.dumps(result) + '\n')
                factory.assert_called_once()
                worker.run_once.assert_awaited_once_with(request_id=request_id)
                worker.close.assert_awaited_once()

    def test_execution_error_is_sanitized_and_closes(self):
        worker = SimpleNamespace(run_once=AsyncMock(side_effect=RuntimeError(SECRET)), close=AsyncMock())
        code, output, _ = self.invoke(['--env-file', str(self.env_file), '--execute'], worker)
        self.assertEqual(code, 1)
        self.assertIn('stopped safely', output)
        worker.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
