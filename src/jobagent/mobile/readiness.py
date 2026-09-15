"""Durable packet review, no model call, automatic application, or owner selector.

Main wiring (0011 is mandatory, never silently fall back):

    snapshot = await capture_preparation_context(repo, job_id, resume_id)
    context = await build_context(...)  # exact same explicit resume_id
    run = await start_run(...)
    await bind_preparation_context(repo, run['id'], resume_id, variant, snapshot)
    # Only now reserve/call the model. Existing successful output_summary retains
    # artifact_ids and documents[{artifact_id, sha256}].

GET jobs/{id}/readiness -> packet_readiness(repo, id)
POST jobs/{id}/review-packet (PacketReviewRequest) -> review_packet(repo, id, body)
Bootstrap/detail -> project_application_state(repo, jobs, applications)
Reject legacy ApplicationCreate(status='ready') before writing; other explicit
records remain supported. Schema request is defined here, so no COLUMNS change.

DB fingerprints cover selected source metadata/Storage version, profile, career
facts, preferences, job and confirmed answer context. Original and selected
artifact bytes are verified again before a review; SQL atomically rechecks the
fingerprint and stores receipt + Ready. Read-derived status invalidates stale
receipts without overwriting external/manual submission history. This does not
cryptographically certify model provenance or an employer's acceptance.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .eligibility_review import decode_review, review_id
from .repository import MobileRepository
from .schemas import MAX_ARTIFACT_BYTES, MAX_RESUME_BYTES

HASH = re.compile(r"^[0-9a-f]{64}$")
EXTERNAL = frozenset({'submitted', 'interviewing', 'rejected', 'withdrawn', 'closed'})
STATUS = EXTERNAL | {'draft', 'ready'}
REASONS = {None, 'packet_review_required', 'review_stale', 'pending_questions', 'eligibility_required', 'too_many_records'}
ISSUES = {None, 'source_missing', 'facts_missing', 'too_many_records', 'generation_context_changed'}


class PacketReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: UUID
    resume_artifact_id: UUID
    letter_artifact_id: UUID
    packet_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: StrictBool


def unavailable(write=False) -> HTTPException:
    return HTTPException(503, detail={
        'code': 'packet_review_unconfirmed' if write else 'readiness_unavailable',
        'message': ('Packet review was not confirmed. Refresh before retrying; a lost response may follow a saved review. '
                    if write else 'Current packet readiness could not be verified. ') +
                   'No automatic submission or retry occurred. Ask the operator to verify migration 0011 and its schema cache if this persists.',
    }, headers={'Retry-After': '5'})


def unique_object(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError('Duplicate key')
        result[k] = v
    return result


def uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('Invalid id')
    return value


def digest(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError('Invalid digest')
    return value


async def rpc(repo: MobileRepository, name: str, payload: dict, *, write=False) -> dict:
    try:
        async with repo.client.stream('POST', '/rest/v1/rpc/' + name, json=payload, follow_redirects=False) as response:
            if response.status_code in (401, 403):
                raise HTTPException(response.status_code, 'Session is invalid or access is denied.')
            if response.status_code in (404, 409, 422):
                # Missing function can be 404 as well; never promote readiness.
                raise HTTPException(response.status_code, 'The packet is unavailable, changed or incomplete. Refresh and review before retrying.')
            if response.status_code == 429:
                retry = response.headers.get('Retry-After', '')
                seconds = min(max(int(retry), 1), 3600) if re.fullmatch(r'[0-9]{1,9}', retry) else 60
                raise HTTPException(429, 'Readiness checks are rate-limited. Retry later.', headers={'Retry-After': str(seconds)})
            if response.status_code != 200:
                raise unavailable(write)
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > 2 * 1024 * 1024:
                    raise unavailable(write)
                raw.extend(chunk)
        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict):
            raise ValueError
        if 'error' in value:
            raise HTTPException(409, 'Confirmed career facts, a current source resume and full job description are required. Review them before preparing.')
        if value.get('user_id') != repo.user_id:
            raise ValueError
        return value
    except (httpx.HTTPError, ValueError, TypeError, RecursionError):
        raise unavailable(write) from None


async def capture_preparation_context(repo: MobileRepository, job_id: str, resume_id: str) -> dict:
    job_id, resume_id = uuid(str(job_id)), uuid(str(resume_id))
    # Resolve owner-filtered IDs before any storage request or context RPC;
    # foreign and absent source IDs remain indistinguishable 404 responses.
    await repo.one('jobs', job_id)
    await repo.one('resumes', resume_id)
    snapshot = await rpc(repo, 'mobile_packet_context', {'p_job_id': job_id, 'p_resume_id': resume_id})
    try:
        if snapshot['job_id'] != job_id or snapshot['resume_id'] != resume_id:
            raise ValueError
        digest(snapshot['context_fingerprint'])
        digest(snapshot['capture_version'])
        source = snapshot['source']
        if type(source['byte_size']) is not int or not 0 < source['byte_size'] <= MAX_RESUME_BYTES:
            raise ValueError
        content = await repo.download('resumes', source['storage_path'], max_bytes=MAX_RESUME_BYTES, fresh=True)
        actual = hashlib.sha256(content).hexdigest()
        if len(content) != source['byte_size'] or (source.get('sha256') is not None and digest(source['sha256']) != actual):
            raise HTTPException(409, 'The stored source resume differs from its saved version. Review or upload the correct source; no AI was called.')
        return {**snapshot, 'resume_sha256': actual}
    except (KeyError, TypeError, ValueError):
        raise unavailable() from None


async def bind_preparation_context(repo: MobileRepository, run_id: str, resume_id: str, variant: str, snapshot: dict) -> None:
    try:
        if snapshot['user_id'] != repo.user_id or snapshot['resume_id'] != str(resume_id):
            raise ValueError
        params = {'p_run_id': uuid(str(run_id)), 'p_resume_id': uuid(str(resume_id)), 'p_variant': variant,
                  'p_context_fingerprint': digest(snapshot['context_fingerprint']), 'p_resume_sha256': digest(snapshot['resume_sha256']),
                  'p_capture_version': digest(snapshot['capture_version'])}
        result = await rpc(repo, 'mobile_bind_packet_context', params, write=True)
        if result.get('run_id') != str(run_id) or result.get('bound') is not True:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise unavailable(True) from None


def checked_readiness(value: dict, owner: str, job_id: str) -> dict:
    """Finite, strict identifiers and booleans; reject malformed/foreign results."""
    if value.get('user_id') != owner or value.get('job_id') != job_id or value.get('version') != 'packet-v1':
        raise ValueError
    if type(value.get('ready')) is not bool or value.get('reason') not in REASONS:
        raise ValueError
    if value.get('application_status') not in STATUS or value.get('recorded_status') not in STATUS | {None}:
        raise ValueError
    if value.get('application_id') is not None:
        uuid(value['application_id'])
    if type(value.get('pending_questions')) is not int or not 0 <= value['pending_questions'] <= 201:
        raise ValueError
    packets = value.get('packets')
    if not isinstance(packets, list) or len(packets) > 400:
        raise ValueError
    seen = set()
    for p in packets:
        run_id, source_id = uuid(p['run_id']), uuid(p['resume_id'])
        if p['format'] not in ('pdf', 'docx') or p['key'] != run_id + ':' + p['format'] or p['key'] in seen:
            raise ValueError
        seen.add(p['key'])
        if p['variant'] not in ('role_aligned', 'career_change', 'sse', 'fde') or type(p['current']) is not bool or p['issue'] not in ISSUES:
            raise ValueError
        if p['current'] != (p['issue'] is None):
            raise ValueError
        for k in ('packet_fingerprint', 'context_fingerprint', 'source_sha256'):
            digest(p[k])
        if not isinstance(p.get('generated_at'), str) or len(p['generated_at']) > 64:
            raise ValueError
        pair = p['artifacts']
        if not isinstance(pair, list) or len(pair) != 2 or pair[0]['id'] == pair[1]['id']:
            raise ValueError
        for a, kind in zip(pair, ('tailored_resume', 'cover_letter')):
            uuid(a['id']); digest(a['sha256'])
            mime = 'application/pdf' if p['format'] == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            if a.get('job_id') != job_id or a.get('resume_id') != source_id or a.get('kind') != kind or a.get('mime_type') != mime:
                raise ValueError
            if type(a.get('byte_size')) is not int or not 0 < a['byte_size'] <= MAX_ARTIFACT_BYTES:
                raise ValueError
            if not isinstance(a.get('filename'), str) or not 1 <= len(a['filename']) <= 255:
                raise ValueError
    review = value.get('review')
    if review is not None:
        for k in ('id', 'run_id', 'resume_artifact_id', 'letter_artifact_id'):
            uuid(review[k])
        digest(review['packet_fingerprint'])
        if type(review['current']) is not bool or not isinstance(review.get('reviewed_at'), str):
            raise ValueError
    if value['ready'] and (review is None or review['current'] is not True or value['pending_questions']
                           or value['reason'] is not None or value['application_status'] != 'ready'
                           or value['recorded_status'] != 'ready' or value['application_id'] is None):
        raise ValueError
    if value['ready'] and not any(p['current'] and p['run_id'] == review['run_id']
                                 and p['packet_fingerprint'] == review['packet_fingerprint']
                                 and p['artifacts'][0]['id'] == review['resume_artifact_id']
                                 and p['artifacts'][1]['id'] == review['letter_artifact_id'] for p in packets):
        raise ValueError
    if not value['ready'] and value['application_status'] == 'ready':
        raise ValueError
    return value


async def packet_readiness(repo: MobileRepository, job_id: str) -> dict:
    job_id = uuid(str(job_id))
    result = await rpc(repo, 'mobile_packet_readiness', {'p_job_id': job_id})
    try:
        return checked_readiness(result, repo.user_id, job_id)
    except (ValueError, KeyError, TypeError):
        raise unavailable() from None


async def review_packet(repo: MobileRepository, job_id: str, body: PacketReviewRequest) -> dict:
    job_id = uuid(str(job_id))
    if body.confirmed is not True or body.resume_artifact_id == body.letter_artifact_id:
        raise HTTPException(422, 'Explicit review of a distinct resume and cover letter is required.')
    state = await packet_readiness(repo, job_id)
    packet = next((p for p in state['packets'] if p['run_id'] == str(body.run_id)
                   and p['packet_fingerprint'] == body.packet_fingerprint and p['current']
                   and p['artifacts'][0]['id'] == str(body.resume_artifact_id)
                   and p['artifacts'][1]['id'] == str(body.letter_artifact_id)), None)
    if packet is None or state['pending_questions'] or state['reason'] in {'eligibility_required', 'too_many_records'}:
        raise HTTPException(409, 'Selected packet or readiness requirements changed. Refresh and review before saving Ready.')
    if state['application_status'] in EXTERNAL:
        raise HTTPException(409, 'External application progress is preserved; it cannot be replaced with Ready.')
    job = await repo.one('jobs', job_id)
    eligibility = decode_review(await repo.one('mobile_answers', review_id(repo.user_id, job_id), required=False), job, repo.user_id)
    if not eligibility or eligibility.get('status') != 'eligible' or eligibility.get('confirmed') is not True:
        raise HTTPException(409, 'A current job-specific eligible self-report is required.')
    snapshot = await capture_preparation_context(repo, job_id, packet['resume_id'])
    if snapshot['context_fingerprint'] != packet['context_fingerprint'] or snapshot['resume_sha256'] != packet['source_sha256']:
        raise HTTPException(409, 'The source or career context changed. Prepare and review a current packet.')
    for selected in packet['artifacts']:
        row = await repo.one('artifacts', selected['id'])
        if any(row.get(k) != selected.get(k) for k in ('id', 'job_id', 'resume_id', 'kind', 'filename', 'mime_type', 'byte_size')):
            raise HTTPException(409, 'Selected document metadata changed. Refresh and review.')
        content = await repo.download('application-artifacts', row['storage_path'], max_bytes=MAX_ARTIFACT_BYTES, fresh=True)
        if len(content) != selected['byte_size'] or hashlib.sha256(content).hexdigest() != selected['sha256']:
            raise HTTPException(409, 'Selected document bytes changed. No Ready receipt was saved.')
    result = await rpc(repo, 'mobile_review_packet', {
        'p_job_id': job_id, 'p_run_id': str(body.run_id), 'p_resume_artifact_id': str(body.resume_artifact_id),
        'p_letter_artifact_id': str(body.letter_artifact_id), 'p_packet_fingerprint': body.packet_fingerprint,
        'p_confirmed': True,
    }, write=True)
    try:
        checked_readiness(result['readiness'], repo.user_id, job_id)
        application = result['application']
        if application['user_id'] != repo.user_id or application['job_id'] != job_id or application['status'] != 'ready':
            raise ValueError
        uuid(application['id'])
        return result
    except (KeyError, TypeError, ValueError):
        raise unavailable(True) from None


async def project_application_state(repo: MobileRepository, jobs: list, applications: list) -> tuple[list, list]:
    """A consistent read projection, not a destructive write or submission claim.

    A capped input might omit an application or review. Preserve the workspace
    and known manual records; fail closed only for derived Ready, explicitly
    flagged as unverified. A narrowed job-detail read can still establish it.
    Only recorded Ready rows require packet queries. Keep finite concurrency.
    """
    capped = len(jobs) >= 200 or len(applications) >= 200
    try:
        for rows in (jobs, applications):
            if len({r['id'] for r in rows}) != len(rows) or any(r.get('user_id') != repo.user_id for r in rows):
                raise ValueError
        if len({a['job_id'] for a in applications}) != len(applications):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise unavailable() from None
    app_by_job = {a['job_id']: dict(a) for a in applications}
    need = [a['job_id'] for a in applications if a['status'] == 'ready']
    limit = asyncio.Semaphore(4)
    async def get(job_id):
        async with limit:
            return job_id, await packet_readiness(repo, job_id)
    states = {} if capped else dict(await asyncio.gather(*(get(i) for i in need)))
    if capped:
        for app in app_by_job.values():
            app['readiness_unavailable'] = 'capped_workspace'
            if app['status'] == 'ready':
                app.update(status='draft', recorded_status='ready')
    for job_id, state in states.items():
        app_by_job[job_id].update(status=state['application_status'], recorded_status=state['recorded_status'], readiness=state)
    result = []
    for original in jobs:
        job = dict(original)
        if capped:
            job['readiness_unavailable'] = 'capped_workspace'
        app = app_by_job.get(job['id'])
        if app and app['status'] in EXTERNAL:
            job['status'] = 'applied'
        elif app and app['status'] == 'ready':
            job['status'] = 'ready'
        elif job['status'] == 'ready':
            job['status'] = 'matched' if job.get('score') is not None else 'new'
        if app:
            job['application_status'] = app['status']
        result.append(job)
    return result, [app_by_job[a['job_id']] for a in applications]
