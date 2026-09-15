"""Disposable native PostgreSQL for SQL tests; never accepts a DSN/existing DB.

Uses existing binaries only (optionally JOBPURSUIT_POSTGRES_BIN and a separate
JOBPURSUIT_PSQL executable for server-only binary bundles). No downloads,
Docker, service manager, TCP listener, password, .pgpass or user psqlrc. Every
cluster/socket/data directory is newly allocated under /tmp and removed only
after this cluster is stopped. Reusable by other focused migration tests.
"""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


# Only instances constructed by this process are registered. Never scan /tmp,
# signal a name/PID from process listings, or touch a pre-existing database.
_ACTIVE_CLUSTERS = set()


def cleanup_owned_clusters():
    """Best effort on interpreter exit; failed shutdown keeps the owned data."""
    for cluster in tuple(_ACTIVE_CLUSTERS):
        try:
            cluster.close()
        except BaseException:
            print("Disposable PostgreSQL cleanup failed; owned directory retained", file=sys.stderr)


atexit.register(cleanup_owned_clusters)


def _version_key(directory):
    # Debian/Ubuntu place server binaries outside PATH. Pick 17 before 9, not
    # lexicographic order, while explicit configuration remains first choice.
    name = directory.parent.name
    return tuple(int(part) for part in name.split('.') if part.isdigit())


def postgres_bin() -> Path:
    candidates = []
    configured = os.environ.get("JOBPURSUIT_POSTGRES_BIN")
    if configured:
        candidates.append(Path(configured))
    executable = shutil.which("postgres")
    if executable:
        candidates.append(Path(executable).resolve().parent)
    for base in (Path("/usr/lib/postgresql"), Path("/usr/pgsql")):
        if base.is_dir():
            candidates.extend(sorted(base.glob("*/bin"), key=_version_key, reverse=True))
    for cellar in (Path("/usr/local/Cellar"), Path("/opt/homebrew/Cellar")):
        if cellar.is_dir():
            candidates.extend(sorted(cellar.glob("postgresql*/*/bin"), reverse=True))
    app = Path("/Applications/Postgres.app/Contents/Versions")
    if app.is_dir():
        candidates.extend(sorted(app.glob("*/bin"), reverse=True))
    for directory in candidates:
        client = Path(os.environ.get("JOBPURSUIT_PSQL", str(directory / "psql")))
        if os.access(client, os.X_OK) and all(os.access(directory / command, os.X_OK) for command in ("postgres", "initdb", "pg_ctl")):
            return directory.resolve()
    raise unittest.SkipTest("Native postgres/initdb/pg_ctl/psql unavailable; no installation or Docker launch attempted")


class DisposablePostgres:
    def __init__(self):
        self.bin = postgres_bin()
        self.psql = Path(os.environ.get("JOBPURSUIT_PSQL", str(self.bin / "psql"))).resolve()
        # A short Unix-socket path avoids macOS sockaddr_un path-length limits.
        self.root = Path(tempfile.mkdtemp(prefix="jp-sql-", dir="/tmp"))
        self.data = self.root / "data"
        self.sock = self.root / "socket"
        self.running = False
        self.command_cleanup_failed = False
        self.env = {"PATH": str(self.bin) + ":/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C",
                    "PGPASSFILE": os.devnull, "PGSERVICEFILE": os.devnull}
        _ACTIVE_CLUSTERS.add(self)
        try:
            self.sock.mkdir(mode=0o700)
            self._run("initdb", "-D", str(self.data), "-U", "fixture_admin", "-A", "trust", "--no-locale", "--encoding=UTF8")
            self._run("pg_ctl", "-D", str(self.data), "-l", str(self.root / "postgres.log"),
                      "-o", "-h '' -k " + str(self.sock) + " -p 55432"
                      " -c shared_buffers=16MB -c max_connections=20"
                      " -c max_parallel_workers=2 -c max_parallel_workers_per_gather=0"
                      " -c autovacuum=off", "-w", "-t", "15", "start")
            self.running = True
        except BaseException:
            # pg_ctl may time out just after startup. Only target our own data
            # directory; retain it rather than delete a potentially live cluster.
            if (self.data / "postmaster.pid").exists():
                self.running = True
            self.close()
            raise

    def _run(self, command, *args, sql=None):
        executable = self.psql if command == "psql" else self.bin / command
        # initdb launches a bootstrap postgres child. subprocess.run(timeout=)
        # only kills its direct child; a timed-out bootstrap could otherwise
        # outlive initdb. The new session belongs exclusively to this command.
        process = subprocess.Popen([str(executable), *args], text=True,
                                   stdin=subprocess.PIPE if sql is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=self.env, start_new_session=True)
        try:
            stdout, stderr = process.communicate(input=sql, timeout=30)
        except BaseException:
            try:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate(timeout=5)
            except BaseException:
                # Never remove data under an unconfirmed live command. A
                # detached pg_ctl postmaster is separately stopped via -D.
                self.command_cleanup_failed = True
            raise
        if process.returncode:
            raise AssertionError("Disposable PostgreSQL " + command + " failed:\n" + stderr)
        return stdout

    def execute(self, sql: str) -> str:
        if not self.running:
            raise RuntimeError("Disposable PostgreSQL is not running")
        return self._run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-h", str(self.sock),
                         "-p", "55432", "-U", "fixture_admin", "-d", "postgres", sql=sql)

    def close(self):
        if self.running:
            self._run("pg_ctl", "-D", str(self.data), "-m", "fast", "-w", "-t", "15", "stop")
            self.running = False
        if self.command_cleanup_failed:
            raise RuntimeError("Disposable PostgreSQL command cleanup unconfirmed; owned directory retained")
        # No automatic TemporaryDirectory finalizer: if shutdown fails, preserve
        # the owned directory rather than removing a possibly running cluster.
        if self.root.exists():
            shutil.rmtree(self.root)
        _ACTIVE_CLUSTERS.discard(self)


SUPABASE_TEST_SCHEMAS = r"""
-- Minimal local platform interfaces; product tables/functions/policies come
-- from the REAL migrations, not a reimplementation. This is not hosted parity.
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create schema auth;
create schema storage;
create schema extensions;
create table auth.users(
  id uuid primary key, raw_user_meta_data jsonb not null default '{}',
  email text, phone text, email_confirmed_at timestamptz,
  created_at timestamptz not null default now()
);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;
create function auth.role() returns text language sql stable as $$
  select nullif(current_setting('request.jwt.claim.role', true), '');
$$;
create table storage.buckets(id text primary key, name text, public boolean,
  file_size_limit bigint, allowed_mime_types text[]);
create table storage.objects(id uuid primary key default pg_catalog.gen_random_uuid(),
  bucket_id text references storage.buckets(id), name text not null, owner_id text,
  owner uuid, version text, metadata jsonb, user_metadata jsonb,
  last_accessed_at timestamptz, updated_at timestamptz default now(),
  unique(bucket_id,name));
alter table storage.objects enable row level security;
create function storage.foldername(name text) returns text[] language sql immutable as $$
  select string_to_array(name, '/');
$$;
grant usage on schema public, auth, storage, extensions to anon, authenticated, service_role;
-- Explicit stand-in for Supabase's deployment defaults, then 0002+ tighten
-- grants exactly as authored. No privileges in any existing database change.
alter default privileges in schema public grant select, insert, update, delete on tables to authenticated;
grant select, insert, update, delete on storage.objects to authenticated;
"""
