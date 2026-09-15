#!/usr/bin/env python3
"""Separate operator process. No production mutation without --execute.

Run at least once per minute with an existing supervisor. --request-id resumes
one operator-reviewed blocked request; it never creates an erasure request.
Optional --env-file loads only the named dotenv file, without interpolation or
overriding existing environment values. Keep that file ignored and private.
Blocked requests exit 1 with the existing safe JSON result; no retry is made.
"""
import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import re
import stat
from uuid import UUID

from dotenv.parser import parse_stream

from jobagent.mobile.privacy_worker import PrivacyWorker, WorkerSettings

MAX_ENV_FILE_BYTES = 64 * 1024


class EnvFileError(ValueError):
    """Fixed operator error; never includes a path, value or parser exception."""


def load_env_file(path: Path) -> None:
    """Strict, bounded dotenv parsing; no discovery, interpolation or evaluation.

    Validate the entire file before touching the process environment. Even an
    explicitly empty existing value wins, so a bad supervisor setting cannot
    silently fall back to a different credential or project from the file.
    """
    try:
        # Nonblocking open prevents an accidentally supplied FIFO from hanging
        # the supervisor. Only regular files can supply configuration.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise EnvFileError()
            raw = source.read(MAX_ENV_FILE_BYTES + 1)
        if len(raw) > MAX_ENV_FILE_BYTES:
            raise EnvFileError()
        text = raw.decode('utf-8-sig')
        if '\x00' in text:
            raise EnvFileError()
        values = {}
        # parse_stream gives structured errors without dotenv's warning logger.
        # Its values are literal: ${NAME} and $(command) are never expanded.
        for binding in parse_stream(io.StringIO(text)):
            if binding.error:
                raise EnvFileError()
            if binding.key is None:  # comment/blank line
                continue
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', binding.key) or binding.value is None:
                raise EnvFileError()
            values[binding.key] = binding.value
        for key, value in values.items():
            os.environ.setdefault(key, value)
    except (OSError, UnicodeError, ValueError):
        raise EnvFileError() from None


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--request-id', type=UUID)
    parser.add_argument('--env-file', type=Path,
                        help='Explicit dotenv file (default: none); existing environment values win. No interpolation.')
    args = parser.parse_args(argv)
    try:
        if args.env_file is not None:
            load_env_file(args.env_file)
        settings = WorkerSettings.from_env()
        settings.validate()
    except EnvFileError:
        print('Unable to load env file. Use a readable regular UTF-8 dotenv file of at most 64 KiB with valid assignments. No values logged.')
        return 2
    except ValueError:
        # urlsplit/port validation can embed supplied URL contents in its error.
        # Never echo raw configuration exceptions, even during a dry run.
        print('Privacy worker configuration invalid. Check MOBILE_PRIVACY_SUPABASE_URL and MOBILE_PRIVACY_SUPABASE_SECRET_KEY. No values logged.')
        return 2
    if not args.execute:
        print('Configuration valid. Dry run: no network, export, deletion, or heartbeat performed.')
        return 0
    worker = PrivacyWorker(settings)
    try:
        result = await worker.run_once(request_id=args.request_id)
        print(json.dumps(result))
        return 1 if result.get('state') == 'blocked' else 0
    except Exception:
        print('Privacy worker stopped safely; inspect configuration and queue status. No private payload logged.')
        return 1
    finally:
        await worker.close()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
