#!/usr/bin/env python3
"""Separate operator process. No production mutation without --execute.

Run at least once per minute with an existing supervisor. --request-id resumes
one operator-reviewed blocked request; it never creates an erasure request.
"""
import argparse
import asyncio
import json
from uuid import UUID

from jobagent.mobile.privacy_worker import PrivacyWorker, WorkerSettings


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--request-id', type=UUID)
    args = parser.parse_args()
    settings = WorkerSettings.from_env()
    try:
        settings.validate()
    except ValueError as exc:
        print(str(exc))  # fixed configuration message only, never env values
        return 2
    if not args.execute:
        print('Configuration valid. Dry run: no network, export, deletion, or heartbeat performed.')
        return 0
    worker = PrivacyWorker(settings)
    try:
        print(json.dumps(await worker.run_once(request_id=args.request_id)))
    except Exception:
        print('Privacy worker stopped safely; inspect configuration and queue status. No private payload logged.')
        return 1
    finally:
        await worker.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
