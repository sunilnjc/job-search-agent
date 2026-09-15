"""One authenticated RPC for an atomic profile/career-context patch (JP-024).

Main's route contract::

    return await save_profile(repo, patch=body.model_dump(mode="json", exclude_unset=True))

The SQL function derives ownership from auth.uid(), checks membership and runs
with the caller's existing grants/RLS. Never fall back to separate REST writes.
Omitted fields are preserved; null clears a field (career text -> empty string,
career background -> neutral defaults). Preferences are a separate single-table
save, NOT part of an atomic onboarding transaction across API requests.

Concurrent RPCs serialize on the profile row; supplied fields are last-writer-
wins. There is no client version/CAS contract. A lost response can follow a
fully committed transaction: return an explicit unconfirmed outcome, do not
retry automatically or assert that nothing changed. Refresh before retrying.
"""
from __future__ import annotations

import json
import re

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from .repository import MobileRepository
from .schemas import CareerBackground, ProfileUpdate

RPC_PATH = "/rest/v1/rpc/mobile_save_profile"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RESULT_FIELDS = frozenset(ProfileUpdate.model_fields) | {"user_id", "created_at", "updated_at"}


def _unconfirmed() -> HTTPException:
    return HTTPException(503, detail={
        "code": "profile_save_unconfirmed",
        "message": "Profile save was not confirmed. Refresh your profile before retrying; a lost response may follow a fully committed save. If unavailable, ask the operator to apply 0008_atomic_profile.sql and refresh the API schema cache. No separate writes or automatic retries were attempted.",
    }, headers={"Retry-After": "5"})


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response key")
        result[key] = value
    return result


async def save_profile(repo: MobileRepository, *, patch: dict) -> dict:
    """Return the full merged profile; send only supplied fields and no owner."""
    try:
        if not isinstance(patch, dict):
            raise ValueError
        body = ProfileUpdate.model_validate(patch)
        payload = body.model_dump(mode="json", exclude_unset=True)
        if body.career_background is not None:
            # Background is a replacement, not a nested partial merge; retain
            # existing model defaults/qualification validation exactly.
            payload["career_background"] = body.career_background.model_dump(mode="json")
    except (ValueError, TypeError, ValidationError):
        raise HTTPException(422, "Invalid profile patch. No save was attempted.") from None

    try:
        async with repo.client.stream("POST", RPC_PATH, json={"p_patch": payload}, follow_redirects=False) as response:
            if response.status_code in (401, 403):
                raise HTTPException(response.status_code, "Session is invalid or profile access is denied.")
            if response.status_code == 422:
                raise HTTPException(422, "Invalid profile patch. The atomic save was rejected.")
            if response.status_code == 409:
                raise HTTPException(409, "Profile save conflicted. Refresh your profile before retrying.")
            if response.status_code == 429:
                retry = response.headers.get("Retry-After", "")
                seconds = min(max(int(retry), 1), 3600) if re.fullmatch(r"[0-9]{1,9}", retry) else 60
                raise HTTPException(429, "The profile service is rate-limited. Retry later.", headers={"Retry-After": str(seconds)})
            if response.status_code != 200:
                raise _unconfirmed()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise _unconfirmed()
                raw.extend(chunk)
        result = json.loads(raw, object_pairs_hook=_unique_object)
        if (not isinstance(result, dict) or set(result) != RESULT_FIELDS
                or result.get("user_id") != repo.user_id
                or any(not isinstance(result.get(key), str) for key in ("created_at", "updated_at"))):
            raise ValueError
        ProfileUpdate.model_validate({key: result[key] for key in ProfileUpdate.model_fields})
        # Existing SQL-valid partial backgrounds use the same neutral defaults
        # as bootstrap. A malformed stored value is never silently cleared.
        if result["career_background"] is None:
            raise ValueError
        result["career_background"] = CareerBackground.model_validate(result["career_background"]).model_dump(mode="json")
        return result
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError, RecursionError):
        raise _unconfirmed() from None
