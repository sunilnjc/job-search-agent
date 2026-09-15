"""Privileged, resumable privacy worker. Not imported into the public API.

Only executes jobs claimed from the protected SQL queue. No arbitrary user IDs,
email delivery, provider refunds, or third-party job applications. Run separately
with a server-only secret; default CLI is a configuration-only dry run.
"""
from __future__ import annotations

import hashlib
import base64
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

from .account_privacy import MAX_EXPORT_BYTES
from .repository import _jwt_role

MANAGED_BUCKETS = frozenset({'resumes', 'application-artifacts', 'account-exports'})


class PrivacyBlocked(Exception):
    def __init__(self, code: str = 'upstream_unavailable'):
        self.code = code
        super().__init__(code)  # no response body, candidate data, or key


@dataclass(frozen=True)
class WorkerSettings:
    url: str
    secret: str = field(repr=False)

    @classmethod
    def from_env(cls) -> 'WorkerSettings':
        return cls(os.environ.get('MOBILE_PRIVACY_SUPABASE_URL', ''),
                   os.environ.get('MOBILE_PRIVACY_SUPABASE_SECRET_KEY', ''))

    def validate(self) -> None:
        parsed = urlsplit(self.url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError('Configure an HTTPS Supabase project origin for the privacy worker.')
        if (not self.secret or len(self.secret) > 8192 or re.search(r'\s', self.secret)
                or not (self.secret.startswith('sb_secret_') or _jwt_role(self.secret) == 'service_role')):
            raise ValueError('Configure a server-only Supabase secret for the privacy worker.')


def object_key(user_id: str, item: dict) -> tuple[str, str]:
    """Block unexpected ownership/layout rather than guessing a deletion target."""
    bucket, path = item.get('bucket'), item.get('path')
    if (bucket not in MANAGED_BUCKETS or not isinstance(path, str) or len(path) > 1024
            or item.get('owner_id') not in (None, '', user_id)
            or re.search(r'[\\%\x00-\x1f\x7f]', path)
            or any(part in ('', '.', '..') for part in path.split('/'))
            or path.split('/')[0] != str(UUID(user_id))):
        raise PrivacyBlocked('storage_scope')
    return bucket, path


def object_route(bucket: str, path: str) -> str:
    return '/storage/v1/object/' + bucket + '/' + quote(path, safe='/')


async def default_cancel(user_id: str, request_id: str) -> dict:
    # Service module performs a durable cancellation fence, even for free users.
    # Missing configuration is not an authorization to erase a billable account.
    from .billing import cancel_for_erasure
    return await cancel_for_erasure(user_id, request_id)


class PrivacyWorker:
    def __init__(self, settings: WorkerSettings, *, transport=None,
                 cancel_billing: Callable[[str, str], Awaitable[dict]] = default_cancel):
        settings.validate()
        self.settings = settings
        headers = {'apikey': settings.secret}
        if not settings.secret.startswith('sb_secret_'):
            headers['Authorization'] = 'Bearer ' + settings.secret
        self.client = httpx.AsyncClient(base_url=settings.url.rstrip('/'), headers=headers,
            timeout=httpx.Timeout(45, connect=10), trust_env=False, follow_redirects=False, transport=transport)
        self.cancel_billing = cancel_billing

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, *, allowed=(200, 201, 204), limit=MAX_EXPORT_BYTES, **kwargs):
        try:
            async with self.client.stream(method, path, follow_redirects=False, **kwargs) as response:
                if response.status_code not in allowed:
                    raise PrivacyBlocked()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > limit:
                        raise PrivacyBlocked('too_large')
                    raw.extend(chunk)
                return response.status_code, bytes(raw)
        except httpx.HTTPError:
            raise PrivacyBlocked() from None

    async def rpc(self, name, data=None):
        _, raw = await self.request('POST', '/rest/v1/rpc/' + name, json=data or {}, limit=20 * 1024 * 1024)
        try:
            return json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            raise PrivacyBlocked('invalid_snapshot') from None

    async def snapshot(self, job):
        result = await self.rpc('mobile_privacy_snapshot', {
            'p_request_id': job['id'], 'p_lease_token': job['lease_token'],
        })
        if (not isinstance(result, dict) or result.get('user_id') != job['user_id']
                or not isinstance(result.get('tables'), dict) or not isinstance(result.get('objects'), list)
                or len(result['objects']) > 2000):
            raise PrivacyBlocked('invalid_snapshot')
        for item in result['objects']:
            object_key(job['user_id'], item)
        return result

    async def finish(self, job, state, *, code=None, path=None):
        result = await self.rpc('mobile_privacy_finish', {
            'p_request_id': job['id'], 'p_lease_token': job['lease_token'], 'p_state': state,
            'p_error_code': code, 'p_export_path': path,
        })
        if result is not True:
            raise PrivacyBlocked()

    async def remove_object(self, bucket, path):
        # Official Storage remove API: metadata and blob removed together. Never
        # delete storage.objects via SQL, which leaves the actual blob behind.
        await self.request('DELETE', '/storage/v1/object/' + bucket,
                           json={'prefixes': [path]}, allowed=(200, 204, 404))

    async def export(self, job, snapshot):
        path = f"{job['user_id']}/{job['id']}/account.zip"
        exists = any(item.get('bucket') == 'account-exports' and item.get('path') == path for item in snapshot['objects'])
        if not exists:
            output = io.BytesIO()
            metadata = {key: snapshot[key] for key in ('user_id', 'account', 'tables')}
            metadata['export_version'] = 1
            metadata['file_manifest'] = []
            total = 0
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for item in sorted(snapshot['objects'], key=lambda row: (row['bucket'], row['path'])):
                    bucket, source_path = object_key(job['user_id'], item)
                    if bucket == 'account-exports':
                        continue  # no recursively embedded historical ZIPs
                    _, raw = await self.request('GET', object_route(bucket, source_path), limit=MAX_EXPORT_BYTES-total)
                    total += len(raw)
                    if total > MAX_EXPORT_BYTES-20*1024*1024:
                        raise PrivacyBlocked('too_large')
                    archive_path = bucket + '/' + source_path
                    info = zipfile.ZipInfo(archive_path, date_time=(1980, 1, 1, 0, 0, 0))
                    archive.writestr(info, raw)
                    metadata['file_manifest'].append({'path': archive_path, 'sha256': hashlib.sha256(raw).hexdigest()})
                archive.writestr(zipfile.ZipInfo('account.json', date_time=(1980, 1, 1, 0, 0, 0)),
                                 json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode())
            data = output.getvalue()
            if len(data) > MAX_EXPORT_BYTES:
                raise PrivacyBlocked('too_large')
            # Fail early when the lease was revoked while assembling the ZIP.
            # SQL's storage.objects trigger is the authoritative write fence;
            # this read alone does NOT eliminate the race with erasure.
            await self.snapshot(job)
            await self.request('POST', object_route('account-exports', path), content=data,
                headers={'Content-Type': 'application/zip', 'x-upsert': 'false',
                         'x-metadata': base64.b64encode(json.dumps({'privacy_lease':job['lease_token']}).encode()).decode('ascii')},
                allowed=(200, 201, 409))
        # Lost upload acknowledgments reconcile from a private, worker-only path.
        _, saved = await self.request('GET', object_route('account-exports', path))
        try:
            with zipfile.ZipFile(io.BytesIO(saved)) as archive:
                info = archive.getinfo('account.json')
                if info.file_size > 20*1024*1024 or archive.testzip() is not None:
                    raise ValueError()
                if json.loads(archive.read(info)).get('user_id') != job['user_id']:
                    raise ValueError()
        except (ValueError, KeyError, zipfile.BadZipFile):
            raise PrivacyBlocked('invalid_snapshot') from None
        await self.finish(job, 'complete', path=path)

    async def erase(self, job, snapshot):
        if self.cancel_billing is default_cancel:
            billing_origin = os.environ.get('MOBILE_BILLING_SUPABASE_URL', self.settings.url).rstrip('/')
            if billing_origin != self.settings.url.rstrip('/'):
                # Never cancel billing in one project and erase Auth in another.
                raise PrivacyBlocked('billing_blocked')
        cancellation = await self.cancel_billing(job['user_id'], job['id'])
        if (not isinstance(cancellation, dict) or cancellation.get('status') != 'ready'
                or cancellation.get('request_id') != job['id']):
            raise PrivacyBlocked('billing_blocked')
        # Recheck after the cancellation fence. No Auth removal until ALL owned
        # blobs, including prior exports and interrupted operations, are absent.
        snapshot = await self.snapshot(job)
        for item in snapshot['objects']:
            await self.remove_object(*object_key(job['user_id'], item))
        clean = await self.snapshot(job)
        if clean['objects']:
            raise PrivacyBlocked('upstream_unavailable')
        auth_path = '/auth/v1/admin/users/' + job['user_id']
        await self.request('DELETE', auth_path, json={'should_soft_delete': False}, allowed=(200, 204, 404))
        status, _ = await self.request('GET', auth_path, allowed=(200, 404), limit=64*1024)
        if status != 404:
            raise PrivacyBlocked()
        # SQL verifies no Auth/storage ownership remains before scrubbing subject.
        await self.finish(job, 'complete')

    async def prune_exports(self):
        rows = await self.rpc('mobile_privacy_expired_exports')
        if not isinstance(rows, list) or len(rows) > 100:
            raise PrivacyBlocked('invalid_snapshot')
        for row in rows:
            path = f"{UUID(row['user_id'])}/{UUID(row['id'])}/account.zip"
            if row.get('export_path') != path:
                raise PrivacyBlocked('storage_scope')
            await self.remove_object('account-exports', path)
            await self.rpc('mobile_privacy_prune', {'p_request_id': row['id']})

    async def run_once(self, *, request_id: UUID | None = None):
        await self.rpc('mobile_privacy_heartbeat')
        await self.prune_exports()
        job = await self.rpc('mobile_privacy_claim', {'p_request_id': str(request_id) if request_id else None})
        if job is None:
            return {'state': 'idle'}
        try:
            if not isinstance(job, dict) or job.get('kind') not in ('export', 'erase'):
                raise ValueError()
            for key in ('id', 'user_id', 'lease_token'):
                job[key] = str(UUID(job[key]))
        except (KeyError, ValueError, TypeError):
            raise PrivacyBlocked('invalid_snapshot') from None
        try:
            snapshot = await self.snapshot(job)
            if job['kind'] == 'export':
                await self.export(job, snapshot)
            else:
                await self.erase(job, snapshot)
            return {'state': 'complete', 'request_id': job['id'], 'kind': job['kind']}
        except Exception as exc:
            code = exc.code if isinstance(exc, PrivacyBlocked) else 'upstream_unavailable'
            await self.finish(job, 'blocked', code=code)
            return {'state': 'blocked', 'request_id': job['id'], 'code': code}
