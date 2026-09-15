# Gate 5 — full offline regression

Run the complete Python suite, including six real PostgreSQL migration suites:

```sh
.venv/bin/python -B scripts/regression_gate.py --summary /tmp/new-regression-result.json
```

Dependencies must already be installed (`python -m pip install '.[web]'` on a clean
test machine). Native `postgres`, `initdb`, `pg_ctl` and `psql` are required.
Debian/Ubuntu `/usr/lib/postgresql/*/bin`, Homebrew and Postgres.app are discovered.
For an existing nonstandard runtime, set `JOBPURSUIT_POSTGRES_BIN` and optionally
`JOBPURSUIT_PSQL`; never supply a DSN or production database. The runner does not
read local dotenv files or accept provider/service credentials. Its Python audit
guard rejects real IP traffic, DNS and private workspace files; it is not a
general-purpose OS sandbox for arbitrary subprocesses.

The suite is serial, **not** its internal SQL concurrency assertions. Each SQL
class gets a newly initialized private Unix-socket cluster, with 16 MB shared
buffers and 20 connections (the largest current concurrency test uses six).
Normal teardown and exit cleanup target only objects owned by that Python
process. Failed shutdown retains the directory and fails the gate; no scanning,
service manager, global sysctl, name-based killing or existing databases.
Command timeouts/cancellation kill and reap only the newly created command
process group, including an `initdb` bootstrap child. Unconfirmed reaping also
retains the owned directory instead of deleting data under a potentially live
process. Detached PostgreSQL postmasters are stopped using their exact owned
data directory, never a process-name match.

The result is FAIL for any assertion/setup error, missing SQL suite, skip,
expected failure, partial execution, cancellation, deadline or cleanup failure.
Evidence reports discovered/passed SQL counts separately. The summary path must
be new; existing reports are never overwritten. Default deadline is 600 seconds.
Cancellation is also checked after discovery and at test boundaries, since
Python test code/import handling can catch a signal. Cleanup gets its own bounded
shutdown time; CI's independent 20-minute job limit is the outer hard bound.

## Linux fallback

`.github/workflows/regression-gate.yml` is **manual dispatch only**, one Ubuntu
24.04 job with a 20-minute bound. It uses the runner's PostgreSQL 16 binaries but
does not start the system PostgreSQL service or share a test database. See the
[official runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md).
Actions are commit-pinned, repository permission is read-only, checkout credentials
are not persisted, and no application secrets, external accounts or provider
calls are required. The same job also runs every `web/tests/*.test.mjs` serially
and a production frontend build from the clean checkout. The Python count
summary is printed in the job log and step summary. No Actions artifact/cache
storage is allocated; no environment, dotenv, database or resume artifacts are
uploaded. Job logs/step summaries do not count toward artifact storage allowance
([GitHub billing documentation](https://docs.github.com/en/billing/concepts/product-billing/github-actions)).

Owner review/push and explicit dispatch are needed to execute CI. Creating this
workflow is **not** an executed green result. The run tests the pushed revision,
not uncommitted local changes. Review repository visibility/Actions allowance
before dispatch; the workflow itself does not purchase or allocate paid runners.

The Mac's reproducible `shmget(..., size=56, ...) -> ENOMEM` with no visible
segments is an OS-level runtime blocker. Reducing PostgreSQL buffers does not
remove PostgreSQL's SysV header requirement and is not claimed to cure it.
Use a functioning isolated Linux runtime instead of skipping the SQL assertions.

This gate does not substitute for hosted Supabase parity, browser E2E,
live-provider quality, native iOS/device tests or production deployment checks.
