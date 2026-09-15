"""Disposable native PostgreSQL for SQL tests; never accepts a DSN/existing DB.

Uses existing binaries only (optionally JOBPURSUIT_POSTGRES_BIN and a separate
JOBPURSUIT_PSQL executable for server-only binary bundles). No downloads,
Docker, service manager, TCP listener, password, .pgpass or user psqlrc. Every
cluster/socket/data directory is newly allocated under /tmp and removed only
after this cluster is stopped. Reusable by other focused migration tests.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


def postgres_bin() -> Path:
    candidates = []
    configured = os.environ.get("JOBPURSUIT_POSTGRES_BIN")
    if configured:
        candidates.append(Path(configured))
    executable = shutil.which("postgres")
    if executable:
        candidates.append(Path(executable).resolve().parent)
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
        self.sock.mkdir(mode=0o700)
        self.running = False
        self.env = {"PATH": str(self.bin) + ":/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
        try:
            self._run("initdb", "-D", str(self.data), "-U", "fixture_admin", "-A", "trust", "--no-locale", "--encoding=UTF8")
            self._run("pg_ctl", "-D", str(self.data), "-l", str(self.root / "postgres.log"),
                      "-o", "-h '' -k " + str(self.sock) + " -p 55432", "-w", "-t", "15", "start")
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
        result = subprocess.run([str(executable), *args], input=sql, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=self.env, timeout=30, check=False)
        if result.returncode:
            raise AssertionError("Disposable PostgreSQL " + command + " failed:\n" + result.stderr)
        return result.stdout

    def execute(self, sql: str) -> str:
        if not self.running:
            raise RuntimeError("Disposable PostgreSQL is not running")
        return self._run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-qAt", "-h", str(self.sock),
                         "-p", "55432", "-U", "fixture_admin", "-d", "postgres", sql=sql)

    def close(self):
        if self.running:
            self._run("pg_ctl", "-D", str(self.data), "-m", "fast", "-w", "-t", "15", "stop")
            self.running = False
        # No automatic TemporaryDirectory finalizer: if shutdown fails, preserve
        # the owned directory rather than removing a possibly running cluster.
        if self.root.exists():
            shutil.rmtree(self.root)


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
