"""Artifact persistence with a durable intent and no destructive compensation.

Contract for prepare: generate the artifact UUID/path once, call
persist_artifact(repo, metadata=..., content=...) for each document. Retain all
confirmed results if a later artifact or model audit fails. Surface operation_id
from a 503 as an explicit partial result; recover_artifact reads already-stored
bytes and performs NO AI call. Operations remain owner-scoped in Supabase.
"""
from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import HTTPException

from .repository import MobileRepository
from .schemas import MAX_ARTIFACT_BYTES, safe_filename

TABLE = "mobile_artifact_operations"
REQUIRED = {"id", "job_id", "resume_id", "kind", "storage_path", "filename", "mime_type", "byte_size", "model_provider", "model_name"}


def checked(repo: MobileRepository, op: dict) -> dict:
    try:
        data = op["artifact_data"]
        if not isinstance(data, dict) or not REQUIRED.issubset(data) or set(data) - REQUIRED - {"application_id"}:
            raise ValueError
        if data["id"] != op["id"] or op.get("user_id") != repo.user_id or op.get("state") not in {"upload_pending", "ready"}:
            raise ValueError
        UUID(data["id"])
        UUID(data["job_id"])
        safe_filename(data["filename"])
        if type(data["byte_size"]) is not int or not 0 < data["byte_size"] <= MAX_ARTIFACT_BYTES:
            raise ValueError
        digest = op["content_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError
        repo.object_path("application-artifacts", data["storage_path"])
        return op
    except (ValueError, TypeError, KeyError, HTTPException):
        raise HTTPException(409, "Invalid artifact recovery metadata; no files were changed.") from None


def incomplete(item_id: str) -> HTTPException:
    return HTTPException(503, detail={
        "code": "artifact_recovery_required", "operation_id": item_id,
        "state": "upload_pending",
        "message": "Artifact persistence is not confirmed. No cleanup was performed. Check its recovery record before regenerating; a missing operation or missing upload bytes cannot be recovered without the original content.",
    }, headers={"Retry-After": "5"})


async def _operation(repo: MobileRepository, item_id: str) -> dict | None:
    op = await repo.one(TABLE, item_id, required=False)
    return checked(repo, op) if op else None


async def _bytes(repo: MobileRepository, op: dict) -> bytes | None:
    try:
        content = await repo.download("application-artifacts", op["artifact_data"]["storage_path"], max_bytes=MAX_ARTIFACT_BYTES, fresh=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    if hashlib.sha256(content).hexdigest() != op["content_sha256"]:
        raise HTTPException(409, "Stored artifact differs from the intended content. No file was overwritten or deleted; manual recovery is required.")
    return content


async def _finish(repo: MobileRepository, op: dict) -> dict:
    if await _bytes(repo, op) is None:
        raise HTTPException(409, detail={"code": "artifact_upload_bytes_required", "operation_id": op["id"], "message": "Artifact bytes are not stored. Recovery cannot recreate them. Retry persistence with the same content and ID; do not claim successful generation."})
    row = await repo.one("artifacts", op["id"], required=False)
    if row is None:
        if op["state"] == "ready":
            raise HTTPException(409, "Completed artifact metadata is missing; manual recovery is required.")
        data = op["artifact_data"]
        # Source resume deletion explicitly retains generated documents. Match
        # the artifacts.resume_id FK's ON DELETE SET NULL when a source was
        # removed while this artifact's metadata write was pending.
        if data.get("resume_id") and await repo.one("resumes", data["resume_id"], required=False) is None:
            data = {**data, "resume_id": None}
        try:
            await repo.insert("artifacts", data)
        except HTTPException as exc:
            if exc.status_code in {401, 403, 429}:
                raise
        row = await repo.one("artifacts", op["id"], required=False)
    if row is None:
        raise incomplete(op["id"])
    if row.get("storage_path") != op["artifact_data"]["storage_path"]:
        raise HTTPException(409, "Artifact identity conflicts with recovery metadata; no file was changed.")
    if op["state"] != "ready":
        try:
            await repo._rows("PATCH", TABLE, filters={"id": "eq." + op["id"], "state": "eq.upload_pending"}, data={"state": "ready"})
        except HTTPException:
            pass
        latest = await _operation(repo, op["id"])
        if not latest or latest["state"] != "ready":
            raise incomplete(op["id"])
    return row


async def persist_artifact(repo: MobileRepository, *, metadata: dict, content: bytes) -> dict:
    """Never delete on uncertainty. Caller must retain UUID/path across retries."""
    item_id = str(UUID(metadata["id"]))
    digest = hashlib.sha256(content).hexdigest()
    candidate = checked(repo, {"id": item_id, "user_id": repo.user_id, "state": "upload_pending", "artifact_data": metadata, "content_sha256": digest})
    if len(content) != metadata["byte_size"]:
        raise HTTPException(422, "Artifact byte size does not match metadata.")
    try:
        op = await _operation(repo, item_id)
        if op is None:
            try:
                await repo.insert(TABLE, {key: candidate[key] for key in ("id", "state", "artifact_data", "content_sha256")})
            except HTTPException as exc:
                if exc.status_code in {401, 403, 429}:
                    raise
            op = await _operation(repo, item_id)
        if op is None:
            raise incomplete(item_id)
        if op["artifact_data"] != metadata or op["content_sha256"] != digest:
            raise HTTPException(409, "Artifact operation ID already belongs to different content; no file was changed.")
        if op["state"] == "upload_pending" and await _bytes(repo, op) is None:
            try:
                await repo.upload("application-artifacts", metadata["storage_path"], content, metadata["mime_type"])
            except HTTPException as exc:
                if exc.status_code in {401, 403, 429}:
                    raise
        return await _finish(repo, op)
    except HTTPException as exc:
        if exc.status_code in {401, 403, 409, 422, 429}:
            raise
        raise incomplete(item_id) from None


async def recover_artifact(repo: MobileRepository, artifact_id: str) -> dict:
    try:
        op = await _operation(repo, str(UUID(artifact_id)))
        if op is None:
            raise HTTPException(404, "Artifact operation was not found.")
        return await _finish(repo, op)
    except HTTPException as exc:
        if exc.status_code in {401, 403, 404, 409, 422, 429}:
            raise
        raise incomplete(artifact_id) from None


async def artifact_operations(repo: MobileRepository) -> list:
    rows = await repo.list(TABLE, filters={"state": "eq.upload_pending"}, limit=200, order="updated_at.desc")
    return [{"id": op["id"], "state": op["state"], "job_id": op["artifact_data"]["job_id"],
             "filename": op["artifact_data"]["filename"]} for op in (checked(repo, row) for row in rows)]
