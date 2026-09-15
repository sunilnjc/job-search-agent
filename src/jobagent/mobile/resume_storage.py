"""Durable, caller-owned resume recovery; no local files or background credentials.

The journal is written BEFORE external bytes change. Upload compensation never
deletes objects. Deletion is a forward-only, retryable operation: metadata must
be confirmed absent before deleting bytes. The tombstone retains the exact path
so later retries can reconcile even after a crash or a lost DELETE response.

Use the mobile service's single-worker tenant work guard for all entrypoints.
This is not a distributed transaction; concurrent direct Storage writers or
multiple uncoordinated API workers require separate serialization. An unavailable
service is reported as incomplete, not magically recovered. No automatic/paid
retries: each call does bounded verification; later retries are user initiated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from uuid import UUID, uuid5

from fastapi import HTTPException

from .repository import MobileRepository
from .schemas import MAX_RESUME_BYTES, safe_filename

TABLE = "mobile_resume_operations"
FIELDS = ("id", "storage_path", "original_filename", "mime_type", "byte_size", "label", "role_focus", "is_default")
TYPES = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def incomplete(operation_id: str, action: str) -> HTTPException:
    # Retention is explicit. No claim that cleanup succeeded or is scheduled.
    return HTTPException(503, detail={
        "code": "resume_recovery_required", "operation_id": operation_id,
        "state": action, "retry_path": f"/api/mobile/resumes/{operation_id}/recover",
        "message": "Resume persistence is not confirmed. No automatic cleanup was performed. Check recovery for any retained files; if no operation or upload bytes are found, retry the original upload with the same Idempotency-Key.",
    }, headers={"Retry-After": "5"})


def checked(repo: MobileRepository, operation: dict) -> dict:
    """Journal is owner-writable data, never an authority to delete arbitrary paths."""
    try:
        data = operation["resume_data"]
        if not isinstance(data, dict) or set(data) != set(FIELDS) or data["id"] != operation["id"]:
            raise ValueError
        UUID(data["id"])
        if operation.get("user_id") != repo.user_id or operation.get("state") not in {"upload_pending", "ready", "delete_pending", "deleted"}:
            raise ValueError
        safe_filename(data["original_filename"])
        if operation.get("content_sha256") is not None and TYPES.get(PurePosixPath(data["original_filename"]).suffix.lower()) != data["mime_type"]:
            raise ValueError
        if data["byte_size"] is not None and (type(data["byte_size"]) is not int or not 0 <= data["byte_size"] <= MAX_RESUME_BYTES):
            raise ValueError
        digest = operation.get("content_sha256")
        if digest is None and operation["state"] in {"upload_pending", "ready"}:
            raise ValueError
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError
        repo.object_path("resumes", data["storage_path"])
        return operation
    except (KeyError, ValueError, TypeError, HTTPException):
        raise HTTPException(409, "Invalid saved resume recovery metadata; no files were changed.") from None


async def operation(repo: MobileRepository, item_id: str) -> dict | None:
    row = await repo.one(TABLE, item_id, required=False)
    return checked(repo, row) if row else None


async def remember(repo: MobileRepository, item_id: str, data: dict, digest: str | None, state: str) -> dict:
    existing = await operation(repo, item_id)
    if existing:
        return existing
    try:
        await repo.insert(TABLE, {"id": item_id, "state": state, "resume_data": data, "content_sha256": digest})
    except HTTPException:
        # Lost insert ACK and duplicate races are resolved by owner-scoped reads.
        pass
    saved = await operation(repo, item_id)
    if saved is None:
        raise incomplete(item_id, state)
    return saved


async def transition(repo: MobileRepository, op: dict, state: str) -> dict:
    try:
        await repo._rows("PATCH", TABLE, filters={"id": "eq." + op["id"], "state": "eq." + op["state"]}, data={"state": state})
    except HTTPException:
        pass
    saved = await operation(repo, op["id"])
    if not saved or saved["state"] != state:
        raise incomplete(op["id"], op["state"])
    return saved


async def bytes_if_present(repo: MobileRepository, path: str) -> bytes | None:
    try:
        return await repo.download("resumes", path, max_bytes=MAX_RESUME_BYTES, fresh=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def verify_content(op: dict, content: bytes) -> None:
    expected = op.get("content_sha256")
    if expected is not None and hashlib.sha256(content).hexdigest() != expected:
        raise HTTPException(409, "Stored bytes do not match the original upload; no file was overwritten or deleted. Manual recovery is required.")


async def finish_upload(repo: MobileRepository, op: dict) -> dict:
    if op["state"] not in {"upload_pending", "ready"}:
        raise HTTPException(409, "This upload was deleted or is being deleted. Start a new upload with a new Idempotency-Key.")
    data = op["resume_data"]
    content = await bytes_if_present(repo, data["storage_path"])
    if content is None:
        raise HTTPException(409, detail={"code": "resume_upload_bytes_required", "operation_id": op["id"], "message": "Stored upload bytes are missing. Re-upload the original file with the same Idempotency-Key. No recovery copy is available on the server."})
    verify_content(op, content)
    row = await repo.one("resumes", op["id"], required=False)
    if row is None:
        if op["state"] == "ready":
            raise HTTPException(409, "Completed resume metadata is missing. Manual recovery is required; no record was silently recreated.")
        try:
            await repo.insert("resumes", data)
        except HTTPException as exc:
            row = await repo.one("resumes", op["id"], required=False)
            if row is None and exc.status_code in {401, 403, 429}:
                raise
            if row is None and exc.status_code == 409 and data["is_default"]:
                # First-default races do not change identity or overwrite bytes.
                try:
                    await repo.insert("resumes", {**data, "is_default": False})
                except HTTPException:
                    pass
        row = await repo.one("resumes", op["id"], required=False)
    if row is None:
        raise incomplete(op["id"], "upload_pending")
    if row.get("storage_path") != data["storage_path"]:
        raise HTTPException(409, "Resume identity conflicts with recovery metadata; no file was changed.")
    if op["state"] != "ready":
        await transition(repo, op, "ready")
    return row


async def persist_resume(repo: MobileRepository, *, content: bytes, filename: str, label: str, role_focus: str | None, idempotency_key: str | None = None) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    details = {"filename": filename, "label": label, "role_focus": role_focus, "sha256": digest}
    if idempotency_key is not None:
        try:
            identity = "key:" + str(UUID(idempotency_key))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(422, "Idempotency-Key must be a UUID.") from None
    else:
        # Compatibility for old clients; new clients should supply per-upload UUIDs.
        identity = "content:" + hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
    item_id = str(uuid5(UUID(repo.user_id), "resume-upload:" + identity))
    try:
        saved = await operation(repo, item_id)
        if saved is None:
            defaults = await repo.list("resumes", filters={"is_default": "eq.true"}, limit=1)
            data = {"id": item_id, "storage_path": repo.user_id + "/" + item_id + PurePosixPath(filename).suffix.lower(),
                    "original_filename": filename, "mime_type": TYPES[PurePosixPath(filename).suffix.lower()],
                    "byte_size": len(content), "label": label, "role_focus": role_focus, "is_default": not bool(defaults)}
            saved = await remember(repo, item_id, data, digest, "upload_pending")
        data = saved["resume_data"]
        if saved.get("content_sha256") != digest or any(data.get(key) != value for key, value in {
            "original_filename": filename, "label": label, "role_focus": role_focus,
        }.items()):
            raise HTTPException(409, "Idempotency-Key already belongs to another upload; no file was changed.")
        if saved["state"] != "upload_pending":
            return await finish_upload(repo, saved)
        stored = await bytes_if_present(repo, data["storage_path"])
        if stored is None:
            try:
                await repo.upload("resumes", data["storage_path"], content, data["mime_type"])
            except HTTPException as exc:
                # An uncertain response is NOT permission to delete or overwrite.
                if exc.status_code in {401, 403, 429}:
                    raise
        return await finish_upload(repo, saved)
    except HTTPException as exc:
        if exc.status_code in {401, 403, 409, 422, 429}:
            raise
        raise incomplete(item_id, "upload_pending") from None


async def finish_delete(repo: MobileRepository, op: dict) -> dict:
    data = op["resume_data"]
    row = await repo.one("resumes", op["id"], required=False)
    if row and row.get("storage_path") != data["storage_path"]:
        raise HTTPException(409, "Resume path changed; no file was deleted. Manual recovery is required.")
    if op["state"] not in {"delete_pending", "deleted"}:
        op = await transition(repo, op, "delete_pending")
    deletion_error = None
    if row:
        try:
            await repo.delete("resumes", op["id"])
        except HTTPException as exc:
            deletion_error = exc
    # A read error is uncertainty, never evidence of absence.
    if await repo.one("resumes", op["id"], required=False) is not None:
        if deletion_error and deletion_error.status_code in {401, 403, 429}:
            raise deletion_error
        raise incomplete(op["id"], "delete_pending")
    deletion_error = None
    if await bytes_if_present(repo, data["storage_path"]) is not None:
        try:
            await repo.delete_object("resumes", data["storage_path"])
        except HTTPException as exc:
            deletion_error = exc
    if await bytes_if_present(repo, data["storage_path"]) is not None:
        if deletion_error and deletion_error.status_code in {401, 403, 429}:
            raise deletion_error
        raise incomplete(op["id"], "delete_pending")
    if op["state"] != "deleted":
        await transition(repo, op, "deleted")
    return {"deleted": True, "id": op["id"], "note": "Resume record and stored file removed. Generated artifacts and a recovery tombstone are retained. To restore the resume, upload your original file again with a new Idempotency-Key."}


async def remove_resume(repo: MobileRepository, item_id: str) -> dict:
    try:
        op = await operation(repo, item_id)
        if op is None:
            row = await repo.one("resumes", item_id)
            repo.object_path("resumes", row["storage_path"])
            # Legacy/direct-web rows have no original digest. The intent still
            # persists the exact owner-checked path before either service changes.
            data = {key: row.get(key) for key in FIELDS}
            op = await remember(repo, item_id, data, None, "delete_pending")
        return await finish_delete(repo, op)
    except HTTPException as exc:
        if exc.status_code in {401, 403, 404, 409, 422, 429}:
            raise
        raise incomplete(item_id, "delete_pending") from None


async def recover_resume(repo: MobileRepository, item_id: str) -> dict:
    try:
        op = await operation(repo, item_id)
        if op is None:
            raise HTTPException(404, "Resume operation was not found.")
        return await finish_delete(repo, op) if op["state"] in {"delete_pending", "deleted"} else await finish_upload(repo, op)
    except HTTPException as exc:
        if exc.status_code in {401, 403, 404, 409, 422, 429}:
            raise
        raise incomplete(item_id, "reconciliation_pending") from None


async def resume_operations(repo: MobileRepository) -> list:
    rows = await repo.list(TABLE, filters={"state": "in.(upload_pending,delete_pending)"}, limit=200, order="updated_at.desc")
    return [{"id": op["id"], "state": op["state"], "filename": op["resume_data"]["original_filename"],
             "retry_path": f"/api/mobile/resumes/{op['id']}/recover"} for op in (checked(repo, row) for row in rows)]
