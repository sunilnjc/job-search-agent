"""Owner-authenticated privacy requests; never loads an administrator key.

The separate privacy worker performs exports/erasure. A fresh worker heartbeat
is required by SQL before accepting requests, so a dormant queue cannot pretend
to be a working privacy service. Account operations are not paid features.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .repository import MobileRepository

MAX_EXPORT_BYTES = 64 * 1024 * 1024
UNAVAILABLE = "Account privacy processing is unavailable. Refresh request status or contact support before trying again."
UNCONFIRMED = "Your privacy request was not confirmed. It may already be queued or processing. Refresh request status before retrying; do not assume deletion has stopped."
MESSAGES = {
    "queued": "Request received. Refresh to check progress.",
    "processing": "Your request is being processed. Do not submit it again.",
    "blocked": "Processing needs operator attention. Your request has been retained.",
    "failed": "Processing stopped safely. Contact support with the request ID.",
    "complete": "Request completed.",
}


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErasureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: str = Field(max_length=32)
    email: str = Field(min_length=3, max_length=254)


async def privacy_rpc(repo: MobileRepository, name: str, payload: dict) -> dict:
    try:
        raw = await repo.request(repo.client, "POST", "/rest/v1/rpc/" + name,
                                 json=payload, max_bytes=128 * 1024)
        result = json.loads(raw)
    except HTTPException as exc:
        if exc.status_code in (401, 403, 429):
            raise
        raise HTTPException(503, UNCONFIRMED if name == "mobile_privacy_request" else UNAVAILABLE) from None
    except (ValueError, UnicodeError):
        raise HTTPException(503, UNCONFIRMED if name == "mobile_privacy_request" else UNAVAILABLE) from None
    if not isinstance(result, dict) or result.get("user_id") != repo.user_id:
        raise HTTPException(503, UNCONFIRMED if name == "mobile_privacy_request" else UNAVAILABLE)
    if result.get("error"):
        code = result["error"]
        status, message = {
            "unverified": (403, "Confirm your account email before continuing."),
            "confirmation": (422, "Enter DELETE MY ACCOUNT and your verified account email exactly."),
            "worker_unavailable": (503, "Privacy processing is offline. This request was not queued. Refresh existing requests or contact support."),
            "erasing": (409, "Account deletion is already pending. Contact support for help."),
            "rate_limited": (429, "A recent export already exists. Download it or retry tomorrow."),
            "not_found": (404, "Privacy request not found."),
        }.get(code, (503, UNAVAILABLE))
        raise HTTPException(status, message)
    return result


def public_request(row: dict) -> dict:
    """Explicit projection: storage paths, worker errors and identifiers stay private."""
    try:
        request_id = str(UUID(row["id"]))
        if row["kind"] not in ("export", "erase") or row["state"] not in MESSAGES:
            raise ValueError()
        ready = row["kind"] == "export" and row["state"] == "complete" and bool(row.get("export_path"))
        if ready:
            ready = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
        return {"id": request_id, "kind": row["kind"], "state": row["state"],
                "created_at": row["created_at"], "completed_at": row.get("completed_at"),
                "expires_at": row.get("expires_at"), "download_ready": ready,
                "message": MESSAGES[row["state"]]}
    except (KeyError, ValueError, TypeError):
        raise HTTPException(503, UNAVAILABLE) from None


async def account_status(repo: MobileRepository) -> dict:
    data = await privacy_rpc(repo, "mobile_privacy_status", {})
    rows = data.get("requests")
    if not isinstance(rows, list) or len(rows) > 50:
        raise HTTPException(503, UNAVAILABLE)
    return {"requests": [public_request(row) for row in rows],
            "capability": {"export": True, "erasure": True,
                           "processing_configured": data.get("worker_ready") is True}}


async def request_export(repo: MobileRepository) -> dict:
    data = await privacy_rpc(repo, "mobile_privacy_request", {"p_kind": "export"})
    return public_request(data.get("request", {}))


async def request_erasure(repo: MobileRepository, body: ErasureRequest) -> dict:
    if (body.confirmation != "DELETE MY ACCOUNT" or not repo.verified_email or
            body.email.strip().casefold() != repo.verified_email.casefold()):
        raise HTTPException(422, "Enter DELETE MY ACCOUNT and your verified account email exactly.")
    data = await privacy_rpc(repo, "mobile_privacy_request", {
        "p_kind": "erase", "p_confirmation": body.confirmation, "p_email": body.email.strip(),
    })
    return public_request(data.get("request", {}))


async def download_export(repo: MobileRepository, request_id: UUID) -> bytes:
    data = await privacy_rpc(repo, "mobile_privacy_status", {})
    rows = data.get("requests", [])
    row = next((row for row in rows if row.get("id") == str(request_id)), None)
    if row is None:
        raise HTTPException(404, "Privacy request not found.")
    if not public_request(row)["download_ready"]:
        raise HTTPException(409, "This export is not ready or has expired. Request a new export.")
    # Never follow a URL/path returned by an untrusted record.
    path = f"{repo.user_id}/{request_id}/account.zip"
    if row.get("export_path") != path:
        raise HTTPException(503, UNAVAILABLE)
    return await repo.request(repo.client, "GET", "/storage/v1/object/account-exports/" + path,
                              max_bytes=MAX_EXPORT_BYTES)
