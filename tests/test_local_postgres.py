"""Infrastructure failure injection only: no database, service or network."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

import local_postgres as pg


class DisposablePostgresInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        # Never adopt a real cluster from another test or inspect its data.
        self.owned = set()
        self.stack.enter_context(patch.object(pg, '_ACTIVE_CLUSTERS', self.owned))
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='jp-infra-test-'))
        self.root = Path(self.temp) / 'owned'
        self.root.mkdir()
        self.stack.enter_context(patch.object(pg.tempfile, 'mkdtemp', return_value=str(self.root)))
        self.stack.enter_context(patch.object(pg, 'postgres_bin', return_value=Path('/synthetic/postgres/bin')))
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.commands = self.stack.enter_context(patch.object(pg.DisposablePostgres, '_run', return_value=''))

    def test_socket_directory_failure_is_cleaned_before_initdb(self):
        with patch.object(Path, 'mkdir', side_effect=OSError('Synthetic mkdir failure')):
            with self.assertRaises(OSError):
                pg.DisposablePostgres()
        self.assertFalse(self.root.exists())
        self.assertFalse(self.owned)
        self.commands.assert_not_called()

    def test_initdb_failure_cleans_only_its_fresh_directory(self):
        sentinel = Path(self.temp) / 'unrelated'
        sentinel.write_text('not a cluster')
        self.commands.side_effect = AssertionError('Synthetic initdb ENOMEM')
        with self.assertRaisesRegex(AssertionError, 'ENOMEM'):
            pg.DisposablePostgres()
        self.assertFalse(self.root.exists())
        self.assertEqual(sentinel.read_text(), 'not a cluster')
        self.assertFalse(self.owned)

    def test_start_failure_with_postmaster_pid_stops_owned_cluster(self):
        def command(name, *args, **kwargs):
            if args[-1] == 'start':
                (self.root / 'data').mkdir()
                (self.root / 'data/postmaster.pid').write_text('synthetic marker')
                raise subprocess.TimeoutExpired('synthetic pg_ctl', 30)
            return ''
        self.commands.side_effect = command
        with self.assertRaises(subprocess.TimeoutExpired):
            pg.DisposablePostgres()
        stop = self.commands.call_args_list[-1].args
        self.assertEqual(stop, ('pg_ctl', '-D', str(self.root / 'data'), '-m', 'fast', '-w', '-t', '15', 'stop'))
        self.assertFalse(self.root.exists())
        self.assertFalse(self.owned)

    def test_success_uses_private_unix_socket_and_bounded_resources(self):
        db = pg.DisposablePostgres()
        self.assertIn(db, self.owned)
        start = self.commands.call_args_list[1].args
        options = start[start.index('-o') + 1]
        self.assertIn("-h ''", options)
        self.assertIn(str(db.sock), options)
        self.assertIn('shared_buffers=16MB', options)
        self.assertIn('max_connections=20', options)  # Six real concurrent billing sessions still fit.
        self.assertEqual(db.sock.stat().st_mode & 0o777, 0o700)
        self.assertEqual(db.env['PGPASSFILE'], os.devnull)
        self.assertEqual(db.env['PGSERVICEFILE'], os.devnull)
        self.assertNotIn('PGHOST', db.env)
        db.close()
        db.close()
        stops = [call for call in self.commands.call_args_list if call.args[-1] == 'stop']
        self.assertEqual(len(stops), 1)
        self.assertFalse(self.owned)

    def test_shutdown_failure_retains_data_and_can_be_retried_explicitly(self):
        db = pg.DisposablePostgres()
        self.commands.side_effect = AssertionError('Synthetic shutdown failure')
        with self.assertRaises(AssertionError):
            db.close()
        self.assertTrue(db.running)
        self.assertTrue(self.root.exists())
        self.assertIn(db, self.owned)
        self.commands.side_effect = None
        db.close()
        self.assertFalse(self.root.exists())
        self.assertFalse(self.owned)

    def test_exit_cleanup_is_scoped_and_does_not_hide_failed_stop(self):
        first, second = Mock(), Mock()
        first.close.side_effect = AssertionError('must not echo arbitrary command details')
        self.owned.update((first, second))
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            pg.cleanup_owned_clusters()
        first.close.assert_called_once_with()
        second.close.assert_called_once_with()
        self.assertIn('directory retained', output.getvalue())
        self.assertNotIn('command details', output.getvalue())


class PostgresBinaryDiscoveryTests(unittest.TestCase):
    def test_debian_versions_sort_numerically(self):
        dirs = [Path('/usr/lib/postgresql/9.6/bin'), Path('/usr/lib/postgresql/17/bin'), Path('/usr/lib/postgresql/16/bin')]
        self.assertEqual(sorted(dirs, key=pg._version_key, reverse=True), [dirs[1], dirs[2], dirs[0]])

    def test_debian_without_postgres_on_path(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(pg.shutil, 'which', return_value=None), \
                patch.object(Path, 'is_dir', autospec=True, side_effect=lambda path: str(path) == '/usr/lib/postgresql'), \
                patch.object(Path, 'glob', return_value=[Path('/usr/lib/postgresql/16/bin')]), \
                patch.object(pg.os, 'access', return_value=True):
            self.assertEqual(pg.postgres_bin(), Path('/usr/lib/postgresql/16/bin'))


class PostgresCommandLifecycleTests(unittest.TestCase):
    def setUp(self):
        # Deliberately bypass initdb; every command/process operation is mocked.
        self.db = object.__new__(pg.DisposablePostgres)
        self.db.bin = Path('/synthetic/bin')
        self.db.psql = Path('/synthetic/client/psql')
        self.db.env = {'PATH': '/synthetic/bin', 'PGPASSFILE': os.devnull}
        self.db.command_cleanup_failed = False
        self.process = Mock(pid=999999999, returncode=0)

    def test_command_isolated_and_sql_uses_stdin_not_command_arguments(self):
        self.process.communicate.return_value = ('result\n', '')
        with patch.object(pg.subprocess, 'Popen', return_value=self.process) as start:
            self.assertEqual(self.db._run('psql', '-X', sql='synthetic SQL'), 'result\n')
        self.assertEqual(start.call_args.args[0], ['/synthetic/client/psql', '-X'])
        self.assertTrue(start.call_args.kwargs['start_new_session'])
        self.assertEqual(start.call_args.kwargs['stdin'], subprocess.PIPE)
        self.assertIs(start.call_args.kwargs['env'], self.db.env)
        self.process.communicate.assert_called_once_with(input='synthetic SQL', timeout=30)

    def test_timeout_kills_only_the_created_process_group_and_reaps(self):
        self.process.communicate.side_effect = [subprocess.TimeoutExpired('initdb', 30), ('', '')]
        with patch.object(pg.subprocess, 'Popen', return_value=self.process), \
                patch.object(pg.os, 'killpg') as kill, self.assertRaises(subprocess.TimeoutExpired):
            self.db._run('initdb', '-D', '/synthetic/owned/data')
        kill.assert_called_once_with(self.process.pid, signal.SIGKILL)
        self.assertEqual(self.process.communicate.call_args.kwargs, {'timeout': 5})
        self.assertFalse(self.db.command_cleanup_failed)

    def test_parent_exit_does_not_skip_bootstrap_child_cleanup(self):
        self.process.returncode = 0  # A parent can exit while its child holds a pipe.
        self.process.communicate.side_effect = [KeyboardInterrupt(), ('', '')]
        with patch.object(pg.subprocess, 'Popen', return_value=self.process), \
                patch.object(pg.os, 'killpg') as kill, self.assertRaises(KeyboardInterrupt):
            self.db._run('initdb')
        kill.assert_called_once_with(self.process.pid, signal.SIGKILL)

    def test_unconfirmed_reap_prevents_directory_deletion(self):
        self.process.communicate.side_effect = subprocess.TimeoutExpired('initdb', 30)
        with patch.object(pg.subprocess, 'Popen', return_value=self.process), \
                patch.object(pg.os, 'killpg'), self.assertRaises(subprocess.TimeoutExpired):
            self.db._run('initdb')
        self.db.running = False
        self.db.root = Path('/synthetic/owned')
        with patch.object(pg.shutil, 'rmtree') as remove, self.assertRaisesRegex(RuntimeError, 'retained'):
            self.db.close()
        remove.assert_not_called()

    def test_disappeared_group_is_harmless_and_nonzero_exit_still_fails(self):
        self.process.communicate.side_effect = [subprocess.TimeoutExpired('initdb', 30), ('', '')]
        with patch.object(pg.subprocess, 'Popen', return_value=self.process), \
                patch.object(pg.os, 'killpg', side_effect=ProcessLookupError), self.assertRaises(subprocess.TimeoutExpired):
            self.db._run('initdb')
        self.assertFalse(self.db.command_cleanup_failed)
        self.process.communicate.side_effect = None
        self.process.communicate.return_value = ('', 'synthetic failure')
        self.process.returncode = 1
        with patch.object(pg.subprocess, 'Popen', return_value=self.process), self.assertRaisesRegex(AssertionError, 'synthetic failure'):
            self.db._run('initdb')


if __name__ == '__main__':
    unittest.main()
