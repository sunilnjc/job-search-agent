"""Supabase REST/Storage transport using a fresh authenticated user's bearer.

No service role, founder settings, SQLite, signed public URLs, or shared auth
client. Explicit user filters supplement (and never replace) Supabase RLS.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException

from .schemas import MAX_ARTIFACT_BYTES


def _jwt_role(value: str) -> Optional[str]:
    """Reject accidentally supplied privileged keys; auth is still remote."""
    try:
        part = value.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return payload.get("role") if isinstance(payload, dict) else None
    except (ValueError, IndexError, UnicodeError):
        return None


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    publishable_key: str

    @classmethod
    def from_env(cls) -> "SupabaseSettings":
        def first(*names: str) -> str:
            return next((os.environ[name].strip() for name in names if os.environ.get(name, "").strip()), "")

        settings = cls(
            first("MOBILE_SUPABASE_URL", "BETA_SUPABASE_URL", "SUPABASE_URL"),
            first("MOBILE_SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        try:
            parsed = urlsplit(self.url)
            valid_url = (
                bool(parsed.hostname) and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment and parsed.path in ("", "/")
                and (parsed.scheme == "https" or (
                    parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
                ))
            )
            _ = parsed.port
        except ValueError:
            valid_url = False
        key = self.publishable_key
        valid_key = bool(re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]+", key)) or _jwt_role(key) == "anon"
        if not valid_url or not valid_key or len(key) > 8192 or re.search(r"\s", key):
            raise HTTPException(503, "Mobile backend is not configured.")


COLUMNS = {
    "profiles": "user_id,display_name,phone,base_location,timezone,onboarding_completed_at,created_at,updated_at",
    "candidate_context": "user_id,career_text,career_background",
    "job_preferences": "user_id,target_titles,preferred_locations,preferred_regions,remote_preference,sponsorship_required,work_authorization_notes,minimum_match_score,created_at,updated_at",
    "jobs": "id,user_id,source,source_job_id,source_url,company_name,title,location_text,workplace_type,employment_type,description,eligibility_status,status,exclusion_reason,published_at,last_validated_at,created_at,updated_at",
    "resumes": "id,user_id,label,role_focus,storage_path,original_filename,mime_type,byte_size,is_default,created_at,updated_at",
    "mobile_resume_operations": "id,user_id,state,resume_data,content_sha256,created_at,updated_at",
    "mobile_artifact_operations": "id,user_id,state,artifact_data,content_sha256,created_at,updated_at",
    "artifacts": "id,user_id,job_id,application_id,resume_id,kind,storage_path,filename,mime_type,byte_size,model_provider,model_name,created_at,updated_at",
    "applications": "id,user_id,job_id,status,applied_at,submission_url,notes,created_at,updated_at",
    "mobile_questions": "id,user_id,job_id,prompt,answer,status,remember,created_at,updated_at",
    "mobile_answers": "id,user_id,question,answer,scope,source_question_id,confirmed_at,created_at,updated_at",
    "job_scores": "id,user_id,job_id,score,recommendation,rationale,model_provider,model_name,prompt_version,created_at",
    "model_runs": "id,user_id,job_id,application_id,operation,provider,model_name,status,input_summary,output_summary,error_message,started_at,completed_at,created_at",
}


class MobileRepository:
    def __init__(self, client: httpx.AsyncClient, user_id: str, verified_email: Optional[str] = None):
        self.client = client
        self.user_id = str(UUID(user_id))
        self.verified_email = verified_email

    @staticmethod
    async def request(client: httpx.AsyncClient, method: str, path: str, *, max_bytes: int = 8 * 1024 * 1024, **kwargs: Any) -> bytes:
        try:
            async with client.stream(method, path, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    if response.status_code == 429:
                        retry = response.headers.get("Retry-After", "")
                        seconds = min(max(int(retry), 1), 3600) if re.fullmatch(r"[0-9]{1,9}", retry) else 60
                        raise HTTPException(429, "The data service is rate-limited. Please retry later.", headers={"Retry-After": str(seconds)})
                    if response.status_code in (401, 403):
                        raise HTTPException(response.status_code, "Session is invalid or access is denied.")
                    if response.status_code == 404:
                        raise HTTPException(404, "Requested item was not found.")
                    if response.status_code == 409:
                        raise HTTPException(409, "A conflicting record already exists.")
                    # Supabase Storage can encode not-found in an HTTP 400. Do
                    # not reinterpret arbitrary REST errors or permission errors.
                    if response.status_code == 400 and path.startswith("/storage/v1/object/"):
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 8192:
                                break
                            raw.extend(chunk)
                        else:
                            try:
                                error = json.loads(raw)
                                missing = isinstance(error, dict) and error.get("code") in {"NoSuchKey", "NoSuchObject"} and str(error.get("statusCode")) == "404"
                            except (ValueError, TypeError):
                                missing = False
                            if missing:
                                raise HTTPException(404, "Requested item was not found.")
                    raise HTTPException(502, "The data service could not complete the request.")
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(result) + len(chunk) > max_bytes:
                        raise HTTPException(502, "The data service returned an oversized response.")
                    result.extend(chunk)
                return bytes(result)
        except httpx.HTTPError:
            raise HTTPException(503, "The data service is temporarily unavailable.") from None

    @classmethod
    async def authenticate(cls, client: httpx.AsyncClient) -> "MobileRepository":
        payload = await cls.request(client, "GET", "/auth/v1/user", max_bytes=64 * 1024)
        try:
            user = json.loads(payload)
            if not isinstance(user, dict) or user.get("role") in ("service_role", "supabase_admin"):
                raise ValueError("Invalid user")
            email = user.get("email")
            verified_email = email if (
                user.get("email_confirmed_at") and isinstance(email, str)
                and len(email) <= 254 and re.fullmatch(r"[^\s<>\x00-\x1f\x7f@]+@[^\s<>\x00-\x1f\x7f@]+", email)
            ) else None
            return cls(client, user["id"], verified_email=verified_email)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(401, "Session is invalid or access is denied.") from None

    async def _rows(self, method: str, table: str, *, filters: Optional[Dict[str, str]] = None, data: Any = None, params: Optional[Dict[str, str]] = None, prefer: str = "return=representation") -> list:
        if table not in COLUMNS:
            raise ValueError("Unknown mobile table")
        query = dict(params or {})
        query.update(filters or {})
        query["user_id"] = "eq." + self.user_id
        query.setdefault("select", COLUMNS[table])
        if data is not None:
            def owned(row: dict) -> dict:
                if row.get("user_id", self.user_id) != self.user_id:
                    raise HTTPException(403, "Access is denied.")
                if method == "POST":
                    return {**row, "user_id": self.user_id}
                # 0002 deliberately grants UPDATE only on editable columns.
                # Ownership is a query filter, never an update payload field.
                return {key: value for key, value in row.items() if key != "user_id"}
            data = [owned(row) for row in data] if isinstance(data, list) else owned(data)
        options: Dict[str, Any] = {"params": query, "headers": {"Prefer": prefer}}
        if data is not None:
            options["json"] = data
        raw = await self.request(self.client, method, "/rest/v1/" + table, **options)
        try:
            rows = json.loads(raw) if raw else []
            if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("user_id") != self.user_id for row in rows):
                raise ValueError("Unexpected response")
            return rows
        except (ValueError, TypeError):
            raise HTTPException(502, "The data service returned an invalid response.") from None

    async def list(self, table: str, *, filters: Optional[Dict[str, str]] = None, limit: int = 200, order: str = "created_at.desc", params: Optional[Dict[str, str]] = None) -> list:
        return await self._rows("GET", table, filters=filters, params={"limit": str(limit), "order": order, **(params or {})})

    async def one(self, table: str, item_id: Optional[str] = None, *, required: bool = True) -> Optional[dict]:
        rows = await self._rows("GET", table, filters={"id": "eq." + str(UUID(str(item_id)))} if item_id else None, params={"limit": "1"})
        if not rows:
            if required:
                raise HTTPException(404, "Requested item was not found.")
            return None
        return rows[0]

    async def insert(self, table: str, data: Any, *, conflict: Optional[str] = None) -> list:
        return await self._rows("POST", table, data=data, params={"on_conflict": conflict} if conflict else None, prefer="return=representation,resolution=merge-duplicates" if conflict else "return=representation")

    async def update(self, table: str, item_id: str, data: dict) -> dict:
        rows = await self._rows("PATCH", table, filters={"id": "eq." + str(UUID(str(item_id)))}, data=data)
        if not rows:
            raise HTTPException(404, "Requested item was not found.")
        return rows[0]

    async def save_candidate_context(self, data: dict) -> dict:
        """PATCH only supplied fields; avoid owner-column upserts and lost fields."""
        if not data or set(data) - {"career_text", "career_background"}:
            raise ValueError("Invalid candidate-context update")
        rows = await self._rows("PATCH", "candidate_context", data=data)
        if not rows:
            try:
                rows = await self.insert("candidate_context", data)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                # A concurrent first save created the singleton. Preserve any
                # fields that request supplied but this request omitted.
                rows = await self._rows("PATCH", "candidate_context", data=data)
        if len(rows) != 1:
            raise HTTPException(502, "The data service did not confirm the saved context.")
        return rows[0]

    async def delete(self, table: str, item_id: str) -> None:
        rows = await self._rows("DELETE", table, filters={"id": "eq." + str(UUID(str(item_id)))})
        if not rows:
            raise HTTPException(404, "Requested item was not found.")

    def object_path(self, bucket: str, storage_path: str) -> str:
        if bucket not in ("resumes", "application-artifacts") or not isinstance(storage_path, str):
            raise HTTPException(404, "Requested file was not found.")
        parts = storage_path.split("/")
        if len(parts) < 2 or parts[0] != self.user_id or any(not part or part in (".", "..") or re.search(r"[\\%\x00-\x1f\x7f]", part) for part in parts):
            raise HTTPException(404, "Requested file was not found.")
        return "/storage/v1/object/" + bucket + "/" + quote(storage_path, safe="/")

    async def download(self, bucket: str, storage_path: str, *, max_bytes: int = MAX_ARTIFACT_BYTES, fresh: bool = False) -> bytes:
        options = {}
        if fresh:
            from uuid import uuid4
            options = {"params": {"reconcile_nonce": str(uuid4())}, "headers": {"Cache-Control": "no-cache, no-store"}}
        return await self.request(self.client, "GET", self.object_path(bucket, storage_path), max_bytes=max_bytes, **options)

    async def upload(self, bucket: str, storage_path: str, content: bytes, mime_type: str) -> None:
        await self.request(self.client, "POST", self.object_path(bucket, storage_path), content=content, headers={"Content-Type": mime_type, "x-upsert": "false"}, max_bytes=64 * 1024)

    async def delete_object(self, bucket: str, storage_path: str) -> None:
        await self.request(self.client, "DELETE", self.object_path(bucket, storage_path), max_bytes=64 * 1024)
