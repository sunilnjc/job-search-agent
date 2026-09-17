"""Native Job Pursuit ASGI API, isolated from the founder application.

Entrypoint: ``uvicorn jobagent.mobile.app:app --workers 1``. AI usage reservations
are database-backed; request/concurrency limits are process-local and need a
shared gateway limit when scaled. Persistence uses the caller's Supabase token.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import importlib
import io
import json
import math
import os
import re
import stat
import time
import unicodedata
import zipfile
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Optional
from urllib.parse import quote
from uuid import UUID, uuid4, uuid5

import httpx
from pydantic import Field, ValidationError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .repository import COLUMNS, MobileRepository, SupabaseSettings, _jwt_role
from .resume_storage import persist_resume, remove_resume, recover_resume, resume_operations
from .artifact_storage import persist_artifact, recover_artifact, artifact_operations
from .budgets import PreAuthLimiter, require_mobile_access, reserve_ai_usage
from .schemas import (
    MAX_ARTIFACT_BYTES, MAX_BODY_BYTES, MAX_RESUME_BYTES, MAX_TEXT_CHARS,
    ApplicationCreate, CareerBackground, ChatRequest, ChatResult, EligibilityReview, FollowUpQuestions, JobCreate, JobUpdate,
    PreferencesUpdate, PrepareRequest, ProfileUpdate, QuestionAnswer,
    RankRequest, RankResult, ResumeUpload, safe_filename,
)
from .eligibility_review import REVIEW_QUESTION, decode_review, job_fingerprint, review_id
from .account_privacy import (EmptyRequest, ErasureRequest, account_status,
    request_export, request_erasure, download_export)
from .profile_store import save_profile as save_atomic_profile
from .discovery import DiscoveryConfig, DiscoveryError, DiscoverySearchRequest, DiscoveryService
from .release import public_release
from .observability import WorkflowTelemetry
from .readiness import (PacketReviewRequest, capture_preparation_context,
    bind_preparation_context, packet_readiness, review_packet, project_application_state)
from .document_review import (DocumentReviewError, validated_document_review,
    validated_saved_document_review)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILE_TYPES = {".pdf": PDF_MIME, ".docx": DOCX_MIME, ".txt": "text/plain", ".md": "text/markdown", ".json": "application/json"}


class CheckoutRequest(EmptyRequest):
    plan_key: str = Field(strict=True, min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")


class BoundedBodyMiddleware:
    """Bound streamed bodies too, before JSON/base64 parsing allocates memory."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def safe_send(message: dict) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                ]
            await send(message)

        headers = dict(scope.get("headers", []))
        limit = MAX_BODY_BYTES if scope["path"] == "/api/mobile/resumes" else 256 * 1024
        if scope["path"] == "/api/mobile/discovery/search":
            limit = 8 * 1024
        try:
            length = int(headers.get(b"content-length", b"0"))
            if length < 0:
                raise ValueError
        except ValueError:
            await JSONResponse({"detail": "Invalid request length."}, status_code=400)(scope, receive, safe_send)
            return
        if length > limit:
            await JSONResponse({"detail": "Request body is too large."}, status_code=413)(scope, receive, safe_send)
            return
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            await JSONResponse({"detail": "Encoded request bodies are not supported."}, status_code=415)(scope, receive, safe_send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await JSONResponse({"detail": "Request body is too large."}, status_code=413)(scope, receive, safe_send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> dict:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, safe_send)


class TenantLimits:
    def __init__(self, requests: int, ai: int, uploads: int, max_tenants: int = 10_000, max_workers: int = 4):
        self.limits = {"requests": requests, "ai": ai, "uploads": uploads}
        self.events: dict = {}
        self.active: set = set()
        self.max_tenants = max_tenants
        self.max_workers = max_workers

    def check(self, user_id: str, category: str = "requests") -> None:
        now = time.monotonic()
        if user_id not in self.events:
            stale = [key for key, state in self.events.items() if state["last"] <= now - 3600 and key not in self.active]
            for key in stale:
                del self.events[key]
            if len(self.events) >= self.max_tenants:
                raise HTTPException(503, "The mobile service is busy. Please retry later.")
            self.events[user_id] = {"last": now, **{key: deque() for key in self.limits}}
        state = self.events[user_id]
        state["last"] = now
        events = state[category]
        while events and events[0] <= now - 3600:
            events.popleft()
        if len(events) >= self.limits[category]:
            retry = max(1, math.ceil(events[0] + 3600 - now))
            raise HTTPException(429, "Hourly request limit reached. Please retry later.", headers={"Retry-After": str(retry)})
        events.append(now)

    @asynccontextmanager
    async def work(self, user_id: str, category: str):
        if user_id in self.active or len(self.active) >= self.max_workers:
            raise HTTPException(429, "Document or AI work is already in progress. Please retry shortly.", headers={"Retry-After": "5"})
        self.check(user_id, category)
        self.active.add(user_id)
        try:
            yield
        finally:
            self.active.discard(user_id)


def validate_resume_file(content: bytes, filename: str) -> str:
    """Validate actual formats before the studio parser sees untrusted bytes."""
    try:
        safe_filename(filename)
        if not content or len(content) > MAX_RESUME_BYTES:
            raise ValueError("Invalid size")
        suffix = PurePosixPath(filename).suffix.lower()
        if suffix == ".pdf":
            if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
                raise ValueError("Invalid PDF")
            # The studio performs structural PDF validation/extraction in its
            # resource-limited subprocess. Never parse hostile PDFs in this worker.
            return PDF_MIME
        if suffix != ".docx" or not content.startswith(b"PK\x03\x04"):
            raise ValueError("Invalid document")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if not 1 <= len(entries) <= 512 or len(set(names)) != len(names):
                raise ValueError("Invalid archive")
            if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                raise ValueError("Invalid DOCX")
            total = 0
            for entry in entries:
                parts = entry.filename.rstrip("/").split("/")
                total += entry.file_size
                if (
                    entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or any(part in ("", ".", "..") for part in parts)
                    or re.search(r"[\\:\x00-\x1f\x7f]", entry.filename)
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or total > 20 * 1024 * 1024 or entry.file_size > 8 * 1024 * 1024
                    or entry.file_size > max(1, entry.compress_size) * 200
                    or entry.filename.lower().endswith((".exe", ".dll", ".bin"))
                ):
                    raise ValueError("Unsafe archive")
                if entry.filename.endswith((".xml", ".rels")):
                    xml = archive.read(entry).upper().replace(b"\x00", b"")
                    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                        raise ValueError("Unsafe XML")
            if archive.testzip() is not None:
                raise ValueError("Corrupt archive")
        return DOCX_MIME
    except Exception:
        raise HTTPException(422, "Use a valid, unencrypted PDF or DOCX resume of at most 8 MiB.") from None


def studio_module(app: FastAPI) -> Any:
    if app.state.studio is not None:
        return app.state.studio
    try:
        return importlib.import_module("jobagent.mobile.studio")
    except Exception:
        raise HTTPException(503, "Document and AI services are not configured.") from None


async def extract_text(studio: Any, content: bytes, filename: str) -> str:
    await run_in_threadpool(validate_resume_file, content, filename)
    try:
        value = await run_in_threadpool(studio.extract_resume_text, content, filename)
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_CHARS:
            raise ValueError("Invalid extracted text")
        return value.strip()
    except Exception as exc:
        # Only forward this fixed parser-policy message, never arbitrary parser
        # output or document text. Inspection still runs in the bounded worker.
        from .pdf_safety import PDF_REJECTION_MESSAGE
        if str(exc) == PDF_REJECTION_MESSAGE:
            raise HTTPException(422, PDF_REJECTION_MESSAGE) from None
        raise HTTPException(422, "Resume text could not be read. Upload a text-based PDF or DOCX.") from None


def first_row(rows: list) -> dict:
    if len(rows) != 1:
        raise HTTPException(502, "The data service did not confirm the saved record.")
    return rows[0]


async def jobs_with_scores(repo: MobileRepository, item_id: Optional[str] = None, *, summaries: bool = False) -> list:
    columns = COLUMNS["jobs"].split(",")
    if summaries:
        columns.remove("description")
    rows = await repo.list("jobs", filters={"id": "eq." + str(item_id)} if item_id else None, params={
        "select": ",".join(columns) + ",job_scores(score,rationale,created_at)",
        "job_scores.order": "created_at.desc",
        "job_scores.limit": "1",
        "job_scores.user_id": "eq." + repo.user_id,
    })
    for row in rows:
        if summaries:
            row["description"] = None  # Fetch full text only when a detail view opens.
        scores = row.pop("job_scores", None) or []
        row["score"] = None
        row["rationale"] = None
        if scores:
            try:
                score = float(scores[0]["score"])
                rationale = scores[0].get("rationale")
                if not math.isfinite(score) or not 0 <= score <= 10 or (rationale is not None and not isinstance(rationale, str)):
                    raise ValueError("Invalid score")
                row.update(score=score, rationale=rationale[:8000] if rationale is not None else None)
            except (ValueError, TypeError, KeyError, IndexError):
                raise HTTPException(502, "The data service returned an invalid score.") from None
    return rows


async def build_context(repo: MobileRepository, studio: Any, *, job_id: Optional[str] = None, resume_id: Optional[str] = None) -> dict:
    profile, career, preferences, memories, answered = await asyncio.gather(
        repo.one("profiles", required=False), repo.one("candidate_context", required=False),
        repo.one("job_preferences", required=False), repo.list("mobile_answers", limit=100, order="updated_at.desc", filters={"scope": "in.(profile,job:" + job_id + ")" if job_id else "eq.profile"}),
        repo.list("mobile_questions", limit=100, order="updated_at.desc", filters={
            "job_id": "eq." + job_id if job_id else "is.null", "status": "eq.answered",
        }),
    )
    job = await repo.one("jobs", job_id) if job_id else None
    if job:
        # Resolve only an explicit review of this exact posting version. Ordinary
        # free-text answers and the mutable jobs column are not proof of rights.
        review_row = await repo.one("mobile_answers", review_id(repo.user_id, job_id), required=False)
        review = decode_review(review_row, job, repo.user_id)
        job["eligibility_confirmed"] = bool(review and review["status"] != "unknown")
        job["eligibility_status"] = review["status"] if review else "unknown"
        job["eligibility_review"] = review
    resume = await repo.one("resumes", resume_id) if resume_id else None
    if resume is None and not resume_id:
        defaults = await repo.list("resumes", filters={"is_default": "eq.true"}, limit=1)
        resume = defaults[0] if defaults else None
    resume_text = ""
    if resume:
        content = await repo.download("resumes", resume["storage_path"], max_bytes=MAX_RESUME_BYTES, fresh=True)
        resume_text = await extract_text(studio, content, resume["original_filename"])
    # "Remember" controls durable answer memory, not whether a user's answer can
    # resolve this application. Transient profile answers never enter job context.
    # Prefer the current answer over potentially stale remembered copies, without
    # changing its job scope or promoting it to verified credentials/declarations.
    scope = "job:" + job_id if job_id else "profile"
    current_answers = []
    for row in answered:
        if row.get("job_id") != job_id or row.get("status") != "answered":
            raise HTTPException(502, "The saved answer metadata is invalid.")
        if (
            not isinstance(row.get("prompt"), str) or not 1 <= len(row["prompt"]) <= 2000
            or not isinstance(row.get("answer"), str) or not row["answer"].strip()
            or len(row["answer"]) > 8000
        ):
            raise HTTPException(502, "The saved answer is invalid. Review your questions.")
        current_answers.append({
            "question": row["prompt"], "answer": row["answer"], "scope": scope,
            "source_question_id": row["id"], "confirmed": True,
            "confirmed_at": row.get("updated_at"),
        })
    answers, seen_sources, seen_prompts = [], set(), set()
    for row in current_answers + memories:
        if row.get("question") == REVIEW_QUESTION:
            continue  # Structured operational metadata is not exportable career prose.
        if not (row.get("confirmed_at") or row.get("confirmed") is True) or row.get("scope") not in ("profile", scope):
            continue
        if not isinstance(row.get("question"), str) or not isinstance(row.get("answer"), str):
            raise HTTPException(502, "The saved answer metadata is invalid.")
        source = row.get("source_question_id")
        key = (row["scope"], prompt_key(row["question"]))
        if (source and source in seen_sources) or key in seen_prompts:
            continue
        if source:
            seen_sources.add(source)
        seen_prompts.add(key)
        answers.append(row)
        if len(answers) == 100:
            break
    profile = {**(profile or {})}
    # Contact email is obtained only from the verified /auth/v1/user response,
    # never from profile input, the stored profile row, or a client JWT payload.
    profile.pop("email", None)
    if repo.verified_email:
        profile["email"] = repo.verified_email
    return {
        "profile": profile, "career_text": (career or {}).get("career_text") or "",
        "career_background": saved_background((career or {}).get("career_background")),
        "preferences": preferences or {}, "resume_text": resume_text,
        "job": job or {}, "answers": answers,
    }


def saved_background(value: Any) -> dict:
    """Default old/empty profiles, but never silently erase malformed saved facts."""
    try:
        return CareerBackground.model_validate({} if value is None else value).model_dump(mode="json")
    except ValidationError:
        raise HTTPException(502, "The saved career background is invalid. Review your profile.") from None


def prompt_key(prompt: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", prompt).casefold().split()).rstrip("?.!").rstrip()


async def persist_questions(repo: MobileRepository, job_id: Optional[str], prompts: Any) -> list:
    """Reuse normalized questions in this tenant/job, including answered rows.

    Stable IDs prevent simultaneous API inserts from duplicating new questions.
    Existing legacy IDs are preserved. No returned/existing row is ever reset to
    pending, overwritten, or deleted because a later model operation failed.
    """
    try:
        validated = FollowUpQuestions.model_validate({"questions": prompts}).questions
    except ValidationError:
        raise HTTPException(502, "The studio returned invalid follow-up questions.") from None
    unique = {}
    for prompt in validated:
        clean = " ".join(unicodedata.normalize("NFKC", prompt).split())
        key = prompt_key(clean)
        if not key or len(clean) > 2000:
            raise HTTPException(502, "The studio returned an empty follow-up question.")
        unique.setdefault(key, clean)
    if not unique:
        return []
    filters = {"job_id": "eq." + job_id if job_id else "is.null"}
    existing = {}
    # Bounded pagination includes older answered rows, not only the latest 200.
    for offset in range(0, 2000, 200):
        rows = await repo.list("mobile_questions", filters=filters, limit=200, order="id.asc", params={"offset": str(offset)})
        for row in rows:
            if row.get("job_id") != job_id or not isinstance(row.get("prompt"), str) or row.get("status") not in ("pending", "answered"):
                raise HTTPException(502, "The saved question metadata is invalid.")
            key = prompt_key(row["prompt"])
            if key not in existing or row["status"] == "answered":
                existing[key] = row
        if len(rows) < 200:
            break
    else:
        raise HTTPException(409, "Too many questions are saved for this job. Review them before requesting more.")
    result = []
    for key, prompt in unique.items():
        if key in existing:
            result.append(existing[key])
            continue
        question_id = str(uuid5(UUID(repo.user_id), "mobile-question:" + (job_id or "profile") + ":" + key))
        try:
            row = first_row(await repo.insert("mobile_questions", {
                "id": question_id, "job_id": job_id, "prompt": prompt,
                "answer": None, "status": "pending", "remember": False,
            }))
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            row = await repo.one("mobile_questions", question_id)
            if row.get("job_id") != job_id or not isinstance(row.get("prompt"), str) or prompt_key(row["prompt"]) != key:
                raise HTTPException(502, "The saved question metadata is invalid.") from None
        result.append(row)
    return result


async def missing_facts(repo: MobileRepository, job_id: Optional[str], exc: Exception, model_run: dict) -> None:
    questions = await persist_questions(repo, job_id, exc.questions)
    await fail_run(repo, model_run, questions=questions,
                   metadata=model_metadata(exc) if getattr(exc, "model_metadata", None) else None)
    raise HTTPException(422, detail={
        "message": "More self-reported career details are needed. Review these questions and confirm the facts before continuing.",
        "questions": questions,
    }) from None


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def model_metadata(result: Any) -> dict:
    """Copy only the studio's documented, non-secret provenance fields."""
    raw = getattr(result, "model_metadata", {})
    metadata = {"provider": "studio", "model_name": "studio-managed", "prompt_version": "mobile-v1"}
    if not isinstance(raw, dict):
        return metadata
    for key in ("provider", "model_name", "prompt_version"):
        value = raw.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", value) and not value.startswith(("sk-", "sb_")):
            metadata[key] = value
    for key in ("input_tokens", "output_tokens"):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 10_000_000:
            metadata[key] = value
    if raw.get("rubric_reason_code") == "rubric_contract_invalid":
        metadata["rubric_reason_code"] = "rubric_contract_invalid"
        for key, minimum, maximum in (("rubric_rejected_count", 1, 12),
                                      ("rubric_unresolved_requirement_count", 0, 100)):
            value = raw.get(key)
            if type(value) is int and minimum <= value <= maximum:
                metadata[key] = value
    return metadata


async def start_run(repo: MobileRepository, operation: str, context: dict, resume_id: Optional[str] = None, **extra: Any) -> dict:
    # Identify the adapter until the result reports the actual provider/model.
    return first_row(await repo.insert("model_runs", {
        "id": str(uuid4()), "job_id": context["job"].get("id"), "operation": operation,
        "provider": "studio", "model_name": "studio-managed", "status": "running",
        "started_at": timestamp(), "input_summary": {
            "resume_id": resume_id, "context_sha256": hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode()).hexdigest(),
            **extra,
        },
    }))


async def fail_run(repo: MobileRepository, model_run: dict, *, questions: Optional[list] = None, metadata: Optional[dict] = None) -> None:
    try:
        data = {
            "status": "failed", "completed_at": timestamp(),
            "error_message": "The document or AI operation could not be completed.",
        }
        if questions is not None:
            data["output_summary"] = {"needs_user": True, "question_ids": [row["id"] for row in questions]}
        if metadata:
            data.update(provider=metadata["provider"], model_name=metadata["model_name"])
            data.setdefault("output_summary", {})["model_metadata"] = metadata
        await repo.update("model_runs", model_run["id"], data)
    except Exception:
        pass  # Never replace the original safe failure or expose provider errors.


def validate_documents(documents: Any) -> list:
    try:
        if not isinstance(documents, list) or not 1 <= len(documents) <= 6:
            raise ValueError("Invalid document count")
        total = 0
        for item in documents:
            if not isinstance(item, dict) or not {"kind", "filename", "mime_type", "content"}.issubset(item):
                raise ValueError("Invalid document")
            safe_filename(item["filename"])
            suffix = PurePosixPath(item["filename"]).suffix.lower()
            if item["kind"] not in ("tailored_resume", "cover_letter", "answer_packet", "other") or FILE_TYPES.get(suffix) != item["mime_type"]:
                raise ValueError("Invalid document type")
            if not isinstance(item["content"], bytes) or not 0 < len(item["content"]) <= MAX_ARTIFACT_BYTES:
                raise ValueError("Invalid document size")
            total += len(item["content"])
        if total > 24 * 1024 * 1024:
            raise ValueError("Invalid aggregate size")
        return documents
    except Exception:
        raise HTTPException(502, "The document service returned invalid output.") from None


async def file_response(repo: MobileRepository, table: str, bucket: str, item_id: UUID) -> Response:
    row = await repo.one(table, str(item_id))
    content = await repo.download(bucket, row["storage_path"], max_bytes=MAX_RESUME_BYTES if table == "resumes" else MAX_ARTIFACT_BYTES)
    filename = row["original_filename" if table == "resumes" else "filename"]
    try:
        safe_filename(filename)
    except (ValueError, TypeError):
        raise HTTPException(502, "The saved file metadata is invalid.") from None
    mime = FILE_TYPES.get(PurePosixPath(filename).suffix.lower(), "application/octet-stream")
    return Response(content, media_type=mime, headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe="")})


def create_app(*, settings: Optional[SupabaseSettings] = None, transport: Optional[httpx.AsyncBaseTransport] = None, studio: Any = None, requests_per_hour: int = 120, ai_per_hour: int = 20, uploads_per_hour: int = 20, max_workers: int = 4, discovery: Any = None, billing: Any = None) -> FastAPI:
    from jobagent.privacy_logging import install_privacy_logging
    install_privacy_logging()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # Uvicorn may configure new handlers after the package was imported.
        install_privacy_logging()
        yield

    application = FastAPI(title="Job Pursuit Mobile API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.add_middleware(BoundedBodyMiddleware)
    application.add_middleware(WorkflowTelemetry)
    application.state.studio = studio
    application.state.discovery = discovery
    application.state.billing = billing
    application.state.limits = TenantLimits(requests_per_hour, ai_per_hour, uploads_per_hour, max_workers=max_workers)
    # A full document-review journey makes several authenticated downloads.
    # Permit that burst while bounding rotating invalid sessions before Auth.
    application.state.pre_auth_limits = PreAuthLimiter(per_peer=120, total=1200)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default includes supplied input (potentially a whole resume).
        return JSONResponse({"detail": "Invalid request. Check field names, values, and size limits."}, status_code=422)

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = request.scope.get("jobpursuit_request_id", "")
        return JSONResponse({"detail": "The mobile service could not complete the request.", "request_id": request_id}, status_code=500, headers={"Cache-Control": "no-store", "X-Request-ID": request_id})

    async def authenticated_repository(request: Request):
        application.state.pre_auth_limits.check(request)
        configured = settings or SupabaseSettings.from_env()
        configured.validate()
        authorization = request.headers.get("authorization", "")
        match = re.fullmatch(r"Bearer ([!-~]{1,8192})", authorization, flags=re.IGNORECASE)
        if not match:
            raise HTTPException(401, "A valid user session is required.", headers={"WWW-Authenticate": "Bearer"})
        token = match.group(1)
        if token == configured.publishable_key or token.startswith("sb_") or _jwt_role(token) in ("anon", "service_role", "supabase_admin"):
            raise HTTPException(401, "A valid user session is required.")
        async with httpx.AsyncClient(
            base_url=configured.url.rstrip("/"),
            headers={"apikey": configured.publishable_key, "Authorization": "Bearer " + token},
            timeout=httpx.Timeout(30, connect=10), follow_redirects=False,
            trust_env=False, transport=transport,
        ) as client:
            repo = await MobileRepository.authenticate(client)
            application.state.limits.check(repo.user_id)
            yield repo

    async def invited_repository(repo: MobileRepository = Depends(authenticated_repository)):
        # Private rollout gate; no client/profile/user-metadata identity claims.
        if "MOBILE_ALLOWED_EMAILS" in os.environ:
            raw = os.environ["MOBILE_ALLOWED_EMAILS"]
            entries = [email.strip().casefold() for email in raw.split(",")]
            if len(raw) > 12800 or len(entries) > 50 or any(
                email and (len(email) > 254 or not re.fullmatch(r"[^\s<>\x00-\x1f\x7f@,*]+@[^\s<>\x00-\x1f\x7f@,*]+", email))
                for email in entries
            ):
                raise HTTPException(503, "Mobile access is not configured.")
            allowed = {email for email in entries if email}
            if not repo.verified_email or repo.verified_email.casefold() not in allowed:
                raise HTTPException(403, "This account is not invited to the private mobile beta.")
        yield repo

    async def repository(repo: MobileRepository = Depends(invited_repository)):
        await require_mobile_access(repo)
        yield repo

    async def privacy_repository(repo: MobileRepository = Depends(authenticated_repository)):
        # Export/deletion must still be reachable after invite revocation or
        # subscription expiry. Auth checks and owner-scoped SQL remain mandatory.
        if not repo.verified_email:
            raise HTTPException(403, "Confirm your account email before continuing.")
        yield repo

    @application.get("/api/mobile/health")
    async def health() -> dict:
        return {"status": "ok", "service": "job-pursuit-mobile"}

    @application.get("/readyz")
    async def readyz():
        """Public probe path. Tunnel should route /readyz here, not the SPA host."""
        try:
            (settings or SupabaseSettings.from_env()).validate()
        except Exception:
            return JSONResponse({"status": "not_ready", "service": "job-pursuit-mobile"}, status_code=503)
        return {"status": "ready", "service": "job-pursuit-mobile", "scope": "configuration"}

    @application.get("/api/mobile/version")
    async def version() -> dict:
        return public_release()

    @application.get("/api/mobile/account")
    async def get_account(repo: MobileRepository = Depends(privacy_repository)) -> dict:
        return await account_status(repo)

    @application.post("/api/mobile/account/exports", status_code=202)
    async def export_account(body: EmptyRequest, repo: MobileRepository = Depends(privacy_repository)) -> dict:
        return await request_export(repo)

    @application.get("/api/mobile/account/exports/{request_id}/download")
    async def export_download(request_id: UUID, repo: MobileRepository = Depends(privacy_repository)) -> Response:
        return Response(await download_export(repo, request_id), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="job-pursuit-account.zip"'})

    @application.post("/api/mobile/account/erasure", status_code=202)
    async def erase_account(body: ErasureRequest, repo: MobileRepository = Depends(privacy_repository)) -> dict:
        return await request_erasure(repo, body)

    @application.post("/api/mobile/discovery/search")
    async def discover_jobs(body: DiscoverySearchRequest, repo: MobileRepository = Depends(repository)) -> dict:
        try:
            if application.state.discovery is None:
                application.state.discovery = DiscoveryService(DiscoveryConfig.from_env())
            preferences = await repo.one("job_preferences", required=False)
            profile, career = await asyncio.gather(repo.one("profiles", required=False), repo.one("candidate_context", required=False))
            if profile is not None:
                profile = {**profile, "career_text": (career or {}).get("career_text"),
                           "career_background": saved_background((career or {}).get("career_background"))}
            result = await application.state.discovery.search(user_id=repo.user_id, preferences=preferences, request=body, profile=profile)
            if result["status"] == "unavailable":
                return JSONResponse({**result, "detail": {"code": "sources_unavailable",
                    "message": "The configured job sources are unavailable. Retry later; no jobs were saved."}}, status_code=503)
            return result
        except DiscoveryError as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}, headers=headers) from None

    def billing_service():
        if application.state.billing is None:
            from .billing import BillingService
            application.state.billing = BillingService.from_env()
        return application.state.billing

    from .billing_customer import customer_billing_router
    application.include_router(customer_billing_router(billing_service, invited_repository))

    @application.get("/api/mobile/billing")
    async def billing_status(repo: MobileRepository = Depends(invited_repository)) -> dict:
        from .billing import billing_capabilities
        capabilities = billing_capabilities(application.state.billing)
        subscription = None
        if capabilities["configuration_ready"]:
            raw = await repo.request(repo.client, "GET", "/rest/v1/mobile_billing_export", max_bytes=32*1024,
                params={"user_id": "eq."+repo.user_id, "limit": "1",
                    "select": "user_id,status,plan_key,period_start,paid_through,cancel_at_period_end,cancellation_status"})
            try:
                rows = json.loads(raw)
                if not isinstance(rows, list) or len(rows)>1 or (rows and rows[0].get("user_id")!=repo.user_id):
                    raise ValueError()
                if rows:
                    subscription = {key: rows[0].get(key) for key in (
                        "status", "plan_key", "period_start", "paid_through", "cancel_at_period_end", "cancellation_status")}
            except (ValueError, TypeError, AttributeError):
                raise HTTPException(503, "Billing status could not be verified. Refresh before changing your subscription.") from None
        return {**capabilities, "subscription": subscription,
                "subscription_status": (subscription or {}).get("status", "none") if capabilities["configuration_ready"] else "not_loaded",
                "message": "Test mode only. Checkout is not proof of payment; access updates after verified billing reconciliation."}

    @application.post("/api/mobile/billing/checkout")
    async def checkout(body: CheckoutRequest, repo: MobileRepository = Depends(invited_repository)) -> dict:
        return await billing_service().checkout(repo, body.plan_key)

    @application.post("/api/mobile/billing/portal")
    async def billing_portal(body: EmptyRequest, repo: MobileRepository = Depends(invited_repository)) -> dict:
        return await billing_service().portal(repo)

    @application.post("/api/mobile/billing/webhook")
    async def billing_webhook(request: Request) -> dict:
        application.state.pre_auth_limits.check(request)
        signatures = [value for key, value in request.scope["headers"]
                      if key.lower() in (b"stripe-signature", b"billing-signature")]
        if len(signatures) != 1 or not 1 <= len(signatures[0]) <= 4096:
            raise HTTPException(400, "One valid billing signature header is required.")
        try:
            signature = signatures[0].decode("ascii")
        except UnicodeDecodeError:
            raise HTTPException(400, "Invalid billing signature header.") from None
        return await billing_service().webhook(await request.body(), signature)

    @application.get("/api/mobile/bootstrap")
    async def bootstrap(repo: MobileRepository = Depends(repository)) -> dict:
        profile, career, preferences, jobs, resumes, artifacts, applications, questions = await asyncio.gather(
            repo.one("profiles", required=False), repo.one("candidate_context", required=False),
            repo.one("job_preferences", required=False), jobs_with_scores(repo, summaries=True), repo.list("resumes"),
            repo.list("artifacts"), repo.list("applications"), repo.list("mobile_questions"),
        )
        if profile is not None:
            profile["career_text"] = (career or {}).get("career_text")
            profile["career_background"] = saved_background((career or {}).get("career_background"))
        if len(jobs) >= 200 or len(applications) >= 200:
            # Preserve the shared capped-list guard; a partial list cannot prove Ready.
            jobs, applications = await project_application_state(repo, jobs, applications)
        else:
            # Validate the complete owner-filtered lists before isolating optional
            # readiness reads. These temporary drafts are a read projection only;
            # neither stored receipts nor external submission history are changed.
            projected_jobs, projected_apps = await project_application_state(repo, jobs, [
                {**row, "status": "draft", "recorded_status": "ready"} if row["status"] == "ready" else row
                for row in applications
            ])
            jobs_by_id = {row["id"]: row for row in projected_jobs}
            apps_by_id = {row["id"]: row for row in projected_apps}
            original_jobs = {row["id"]: row for row in jobs}
            limit = asyncio.Semaphore(4)

            async def check_ready(row):
                job = original_jobs.get(row["job_id"])
                async with limit:
                    return await project_application_state(repo, [job] if job else [], [row])

            async def project_ready(row):
                try:
                    # Include semaphore queue time: optional readiness work must
                    # not consume the browser's entire 15-second bootstrap budget.
                    return await asyncio.wait_for(check_ready(row), timeout=5)
                except asyncio.TimeoutError:
                    pass
                except HTTPException as exc:
                    # Never mask authentication/authorization errors, invalid
                    # aggregate data, or action failures. An unavailable or
                    # untrusted per-role check supplies no readiness authority.
                    if not (exc.status_code == 429 or (exc.status_code == 503
                            and isinstance(exc.detail, dict)
                            and exc.detail.get("code") == "readiness_unavailable")):
                        raise
                unavailable_app = {**apps_by_id[row["id"]], "readiness_unavailable": "packet_check_failed"}
                job = jobs_by_id.get(row["job_id"])
                unavailable_job = {**job, "readiness_unavailable": "packet_check_failed"} if job else None
                return ([unavailable_job] if unavailable_job else []), [unavailable_app]

            projections = await asyncio.gather(*(project_ready(row) for row in applications if row["status"] == "ready"))
            for checked_jobs, checked_apps in projections:
                jobs_by_id.update((row["id"], row) for row in checked_jobs)
                apps_by_id.update((row["id"], row) for row in checked_apps)
            jobs = [jobs_by_id[row["id"]] for row in jobs]
            applications = [apps_by_id[row["id"]] for row in applications]
        return {"profile": profile, "preferences": preferences, "jobs": jobs, "resumes": resumes,
                "artifacts": artifacts, "applications": applications, "questions": questions,
                "capabilities": {"manual_job_import": True, "job_url_fetch": False, "job_detail_fetch": True,
                    "discovery": "profile_rules_v1", "discovery_automatic_on_open": True,
                    "eligibility_review": "job_scoped_user_self_report",
                    "document_variants": ["role_aligned", "career_change", "sse", "fde"],
                    "default_document_variant": "role_aligned", "career_background_self_reported": True,
                    "chat_modes": ["application", "interview", "resume"],
                    "automatic_submission": False, "max_resume_bytes": MAX_RESUME_BYTES,
                    "bootstrap_list_limit": 200, "hourly_request_limit": requests_per_hour,
                    "hourly_ai_limit": ai_per_hour, "hourly_upload_limit": uploads_per_hour,
                    "rate_limit_scope": "database_ai_process_requests", "durable_ai_budget": True,
                    "ai_requires_configuration": True}}

    @application.put("/api/mobile/profile")
    async def save_profile(body: ProfileUpdate, repo: MobileRepository = Depends(repository)) -> dict:
        return await save_atomic_profile(repo, patch=body.model_dump(mode="json", exclude_unset=True))

    @application.put("/api/mobile/preferences")
    async def save_preferences(body: PreferencesUpdate, repo: MobileRepository = Depends(repository)) -> dict:
        data = body.model_dump(mode="json")
        if "discovery_rules" not in body.model_fields_set:
            # Older native clients must not reset newer hard constraints.
            data.pop("discovery_rules")
            previous = await repo.one("job_preferences", required=False)
            if not body.sponsorship_required and (previous or {}).get("discovery_rules", {}).get("sponsorship_policy") == "require_explicit":
                raise HTTPException(422, "Your saved discovery rules require explicit sponsorship. Review those rules in the web preferences before turning off sponsorship.")
        return first_row(await repo.insert("job_preferences", data, conflict="user_id"))

    @application.post("/api/mobile/jobs")
    async def import_job(body: JobCreate, response: Response, repo: MobileRepository = Depends(repository)) -> dict:
        data = body.model_dump(mode="json")
        existing = await repo.list("jobs", filters={"source_url": "eq." + body.source_url}, limit=1)
        duplicate = bool(existing)
        if existing:
            job = existing[0]
            if not job.get("description"):
                # Explicit supplied content completes a legacy web bookmark; it
                # never overwrites a previously reviewed posting silently.
                job = await update_job(UUID(job["id"]), JobUpdate(description=body.description), repo)
        else:
            try:
                job = first_row(await repo.insert("jobs", {**data, "id": str(uuid4()), "source": "manual", "status": "new", "eligibility_status": "unknown"}))
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                existing = await repo.list("jobs", filters={"source_url": "eq." + body.source_url}, limit=1)
                if not existing:
                    raise
                job, duplicate = existing[0], True
        enriched = await jobs_with_scores(repo, job["id"])
        response.status_code = 200 if duplicate else 201
        return {**(enriched[0] if enriched else job), "duplicate": duplicate}

    @application.patch("/api/mobile/jobs/{job_id}")
    async def update_job(job_id: UUID, body: JobUpdate, repo: MobileRepository = Depends(repository)) -> dict:
        previous = await repo.one("jobs", str(job_id))
        data = body.model_dump(exclude_unset=True)
        if any(key != "status" and value != previous.get(key) for key, value in data.items()):
            data["eligibility_status"] = "unknown"
            # User-reported application progress isn't undone by a posting edit.
            if previous["status"] in ("new", "matched", "excluded"):
                data["status"] = "new"
            # Retain historical scores, but make stale recommendations visibly
            # unusable. This is deterministic invalidation, not a new AI score.
            await repo.insert("job_scores", {"job_id": str(job_id), "score": 0.0,
                "recommendation": "review", "rationale": "Posting details changed. Rank this version again before relying on the fit estimate.",
                "model_provider": "system", "model_name": "posting-version-review", "prompt_version": "posting-edit-v1"})
        await repo.update("jobs", str(job_id), data)
        return first_row(await jobs_with_scores(repo, str(job_id)))

    @application.get("/api/mobile/jobs/{job_id}")
    async def job_detail(job_id: UUID, repo: MobileRepository = Depends(repository)) -> dict:
        job = first_row(await jobs_with_scores(repo, str(job_id)))
        projected, _ = await project_application_state(repo, [job], await repo.list("applications", filters={"job_id": "eq." + str(job_id)}))
        job = projected[0]
        review = decode_review(await repo.one("mobile_answers", review_id(repo.user_id, str(job_id)), required=False), job, repo.user_id)
        return {**job, "eligibility_status": review["status"] if review else "unknown", "eligibility_review": review}

    @application.get("/api/mobile/jobs/{job_id}/readiness")
    async def current_readiness(job_id: UUID, repo: MobileRepository = Depends(repository)) -> dict:
        return await packet_readiness(repo, str(job_id))

    @application.post("/api/mobile/jobs/{job_id}/review-packet")
    async def save_packet_review(job_id: UUID, body: PacketReviewRequest, repo: MobileRepository = Depends(repository)) -> dict:
        return await review_packet(repo, str(job_id), body)

    @application.post("/api/mobile/jobs/{job_id}/eligibility")
    async def review_eligibility(job_id: UUID, body: EligibilityReview, repo: MobileRepository = Depends(repository)) -> dict:
        job = await repo.one("jobs", str(job_id))
        item_id = review_id(repo.user_id, str(job_id))
        data = {"question": REVIEW_QUESTION, "scope": "job:" + str(job_id), "source_question_id": None,
                "answer": json.dumps({**body.model_dump(), "job_fingerprint": job_fingerprint(job)})}
        existing = await repo.one("mobile_answers", item_id, required=False)
        if existing:
            await repo.update("mobile_answers", item_id, data)
        else:
            try:
                await repo.insert("mobile_answers", {"id": item_id, **data})
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                await repo.update("mobile_answers", item_id, data)
        await repo.update("jobs", str(job_id), {"eligibility_status": body.status})
        if body.status != "unknown":
            # Complete only our own canonical prompt, not arbitrary ATS/legal
            # declarations that happen to mention authorization.
            canonical = "Are you authorized to work in this job's location, and would you need sponsorship?"
            questions = await repo.list("mobile_questions", filters={"job_id": "eq." + str(job_id), "status": "eq.pending"})
            for question in questions:
                if prompt_key(question.get("prompt", "")) == prompt_key(canonical):
                    await repo.update("mobile_questions", question["id"], {"status": "answered", "remember": False,
                        "answer": "Job-specific self-report: " + body.status + ". " + body.reason})
        return await job_detail(job_id, repo)

    @application.post("/api/mobile/resumes", status_code=201)
    async def upload_resume(body: ResumeUpload, request: Request, repo: MobileRepository = Depends(repository)) -> dict:
        async with application.state.limits.work(repo.user_id, "uploads"):
            try:
                content = base64.b64decode(body.content_base64, validate=True)
            except (binascii.Error, ValueError):
                raise HTTPException(422, "Resume content must be valid base64.") from None
            await extract_text(studio_module(application), content, body.filename)
            return await persist_resume(repo, content=content, filename=body.filename,
                                        label=body.label, role_focus=body.role_focus,
                                        idempotency_key=request.headers.get("Idempotency-Key"))

    @application.get("/api/mobile/resume-operations")
    async def pending_resume_operations(repo: MobileRepository = Depends(repository)) -> list:
        return await resume_operations(repo)

    @application.post("/api/mobile/resumes/{resume_id}/recover")
    async def recover_resume_operation(resume_id: UUID, repo: MobileRepository = Depends(repository)) -> dict:
        async with application.state.limits.work(repo.user_id, "uploads"):
            return await recover_resume(repo, str(resume_id))

    @application.get("/api/mobile/resumes/{resume_id}/download")
    async def download_resume(resume_id: UUID, repo: MobileRepository = Depends(repository)) -> Response:
        return await file_response(repo, "resumes", "resumes", resume_id)

    @application.get("/api/mobile/resumes/{resume_id}/text")
    async def resume_text(resume_id: UUID, repo: MobileRepository = Depends(repository)) -> dict:
        async with application.state.limits.work(repo.user_id, "uploads"):
            row = await repo.one("resumes", str(resume_id))
            content = await repo.download("resumes", row["storage_path"], max_bytes=MAX_RESUME_BYTES)
            text = await extract_text(studio_module(application), content, row["original_filename"])
            # Extraction is an unconfirmed preview. Only a subsequent explicit
            # PUT /profile can save the user's reviewed facts to career_text.
            return {"text": text}

    @application.get("/api/mobile/artifacts/{artifact_id}/download")
    async def download_artifact(artifact_id: UUID, repo: MobileRepository = Depends(repository)) -> Response:
        return await file_response(repo, "artifacts", "application-artifacts", artifact_id)

    @application.delete("/api/mobile/resumes/{resume_id}")
    async def delete_resume(resume_id: UUID, repo: MobileRepository = Depends(repository)) -> dict:
        async with application.state.limits.work(repo.user_id, "uploads"):
            return await remove_resume(repo, str(resume_id))

    @application.post("/api/mobile/applications")
    async def save_application(body: ApplicationCreate, repo: MobileRepository = Depends(repository)) -> dict:
        if body.status == "ready":
            raise HTTPException(422, "Review the current document packet in Application Studio before marking Ready. A status label alone is not a document review.")
        await repo.one("jobs", str(body.job_id))
        # This only records the explicitly supplied status. No ATS call, submitted
        # timestamp, submission URL, or claim of an actual submission is generated.
        return first_row(await repo.insert("applications", body.model_dump(mode="json"), conflict="user_id,job_id"))

    @application.post("/api/mobile/questions/{question_id}/answer")
    async def answer_question(question_id: UUID, body: QuestionAnswer, repo: MobileRepository = Depends(repository)) -> dict:
        question = await repo.one("mobile_questions", str(question_id))
        memory = await repo.list("mobile_answers", filters={"source_question_id": "eq." + str(question_id)}, limit=100)
        saved_memory = None
        if body.remember:
            data = {"source_question_id": str(question_id), "question": question["prompt"], "answer": body.answer,
                    "scope": "job:" + question["job_id"] if question.get("job_id") else "profile"}
            if memory:
                saved_memory = await repo.update("mobile_answers", memory[0]["id"], data)
            else:
                # Stable IDs prevent duplicate memory on concurrent submissions,
                # without requiring an undeclared unique constraint or an upsert
                # that would violate column-level UPDATE grants.
                memory_id = str(uuid5(UUID(repo.user_id), "mobile-answer:" + str(question_id)))
                try:
                    saved_memory = first_row(await repo.insert("mobile_answers", {"id": memory_id, **data}))
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    saved_memory = await repo.update("mobile_answers", memory_id, data)
        elif memory:
            await repo._rows("DELETE", "mobile_answers", filters={"source_question_id": "eq." + str(question_id)})
        try:
            return await repo.update("mobile_questions", str(question_id), {"answer": body.answer, "remember": body.remember, "status": "answered"})
        except Exception:
            try:
                if memory:
                    for previous in memory:
                        writable = {key: previous[key] for key in ("question", "answer", "scope", "source_question_id")}
                        if body.remember:
                            await repo.update("mobile_answers", previous["id"], writable)
                        else:
                            await repo.insert("mobile_answers", {"id": previous["id"], **writable})
                elif saved_memory:
                    await repo.delete("mobile_answers", saved_memory["id"])
            except Exception:
                pass
            raise

    @application.get("/api/mobile/jobs/{job_id}/document-reviews")
    async def document_reviews(job_id: UUID, repo: MobileRepository = Depends(repository)) -> dict:
        await repo.one("jobs", str(job_id))
        runs = await repo.list("model_runs", filters={"job_id": "eq." + str(job_id),
            "operation": "eq.prepare_documents", "status": "eq.succeeded"}, limit=20)
        reviews, unavailable_count = [], 0
        for run in runs:
            summary = run.get("output_summary")
            try:
                review = validated_saved_document_review(summary.get("document_review") if isinstance(summary, dict) else None)
            except DocumentReviewError:
                unavailable_count += 1
                continue
            reviews.append({"model_run_id": run["id"], "created_at": run.get("completed_at") or run.get("created_at"), "review": review})
        return {"reviews": reviews, "unavailable_count": unavailable_count,
            "limit": 20, "possibly_truncated": len(runs) == 20}

    @application.post("/api/mobile/jobs/{job_id}/prepare")
    async def prepare(job_id: UUID, body: PrepareRequest, repo: MobileRepository = Depends(repository)) -> dict:
        async with application.state.limits.work(repo.user_id, "ai"):
            adapter = studio_module(application)
            snapshot = await capture_preparation_context(repo, str(job_id), str(body.resume_id))
            context = await build_context(repo, adapter, job_id=str(job_id), resume_id=str(body.resume_id))
            model_run = await start_run(repo, "prepare_documents", context, str(body.resume_id), variant=body.variant)
            artifacts, documents, questions = [], [], []
            all_persisted = False
            try:
                await bind_preparation_context(repo, model_run["id"], str(body.resume_id), body.variant, snapshot)
                await reserve_ai_usage(repo, "prepare")
                raw_documents = await run_in_threadpool(adapter.prepare_documents, context, body.variant)
                metadata = model_metadata(raw_documents)
                documents = validate_documents(raw_documents)
                document_review = validated_document_review(raw_documents)
                questions = await persist_questions(repo, str(job_id), getattr(raw_documents, "questions", []))
                for document in documents:
                    artifact_id = str(uuid4())
                    storage_path = repo.user_id + "/" + artifact_id + PurePosixPath(document["filename"]).suffix.lower()
                    artifacts.append(await persist_artifact(repo, metadata={
                        "id": artifact_id, "job_id": str(job_id), "resume_id": str(body.resume_id),
                        "kind": document["kind"], "storage_path": storage_path,
                        "filename": document["filename"], "mime_type": document["mime_type"], "byte_size": len(document["content"]),
                        "model_provider": metadata["provider"], "model_name": metadata["model_name"],
                    }, content=document["content"]))
                all_persisted = True
                await repo.update("model_runs", model_run["id"], {"status": "succeeded", "completed_at": timestamp(), "provider": metadata["provider"], "model_name": metadata["model_name"],
                    "output_summary": {"artifact_ids": [row["id"] for row in artifacts], "variant": body.variant,
                        "question_ids": [row["id"] for row in questions],
                        "model_metadata": metadata,
                        "document_review": document_review,
                        "documents": [{"artifact_id": row["id"], "sha256": hashlib.sha256(doc["content"]).hexdigest()} for row, doc in zip(artifacts, documents)]}})
                return {"artifacts": artifacts, "questions": questions,
                    "model_run_id": model_run["id"], "document_review": document_review}
            except Exception as exc:
                if all_persisted:
                    return {"artifacts": artifacts, "questions": questions,
                            "operation_status": "saved_sync_pending", "model_run_id": model_run["id"],
                            "warnings": ["Your documents are saved. Their activity log could not be synchronized; refresh before regenerating."]}
                await fail_run(repo, model_run, metadata=model_metadata(exc))
                if isinstance(exc, HTTPException):
                    if isinstance(exc.detail, dict) and exc.detail.get("operation_id"):
                        raise HTTPException(exc.status_code, detail={**exc.detail,
                            "saved_artifacts": artifacts, "model_run_id": model_run["id"]}, headers=exc.headers) from None
                    raise
                if isinstance(exc, getattr(adapter, "MissingFactsError", ())):
                    await missing_facts(repo, str(job_id), exc, model_run)
                raise HTTPException(502, "Document preparation failed. No successful preparation was recorded.") from None

    @application.get("/api/mobile/artifact-operations")
    async def pending_artifact_operations(repo: MobileRepository = Depends(repository)) -> list:
        return await artifact_operations(repo)

    @application.post("/api/mobile/artifact-operations/{artifact_id}/recover")
    async def recover_artifact_operation(artifact_id: UUID, repo: MobileRepository = Depends(repository)) -> dict:
        # Does not invoke AI or generate replacements. It only reconciles bytes
        # that the original persistence operation already stored.
        return await recover_artifact(repo, str(artifact_id))

    @application.post("/api/mobile/jobs/{job_id}/rank")
    async def rank(job_id: UUID, body: Optional[RankRequest] = None, repo: MobileRepository = Depends(repository)) -> dict:
        async with application.state.limits.work(repo.user_id, "ai"):
            adapter = studio_module(application)
            resume_id = str(body.resume_id) if body and body.resume_id else None
            context = await build_context(repo, adapter, job_id=str(job_id), resume_id=resume_id)
            model_run = await start_run(repo, "rank_job", context, resume_id)
            score_row = None
            score_id = str(uuid4())
            score_attempted = False
            try:
                await reserve_ai_usage(repo, "rank")
                raw_result = await run_in_threadpool(adapter.rank_job, context)
                metadata = model_metadata(raw_result)
                provenance = getattr(raw_result, "model_metadata", {})
                if isinstance(provenance, dict) and provenance.get("status", "validated") != "validated":
                    error = RuntimeError("Unvalidated model ranking")
                    error.model_metadata = provenance
                    raise error
                result = RankResult.model_validate(raw_result)
                questions = await persist_questions(repo, str(job_id), getattr(raw_result, "questions", []))
                score_attempted = True
                score_row = first_row(await repo.insert("job_scores", {
                    "id": score_id,
                    "job_id": str(job_id), **result.model_dump(), "model_provider": metadata["provider"], "model_name": metadata["model_name"], "prompt_version": metadata["prompt_version"],
                }))
                old_status = context["job"]["status"]
                if old_status in ("new", "matched", "excluded"):
                    new_status = "excluded" if result.recommendation == "exclude" else "matched" if result.recommendation in ("match", "strong_match") else "new"
                    await repo._rows("PATCH", "jobs", filters={"id": "eq." + str(job_id), "status": "eq." + old_status}, data={"status": new_status})
                await repo.update("model_runs", model_run["id"], {"status": "succeeded", "completed_at": timestamp(), "provider": metadata["provider"], "model_name": metadata["model_name"], "output_summary": {"job_score_id": score_row["id"], **result.model_dump(), "question_ids": [row["id"] for row in questions], "model_metadata": metadata}})
                return first_row(await jobs_with_scores(repo, str(job_id)))
            except Exception as exc:
                if score_row is None and score_attempted:
                    # A lost INSERT acknowledgement is not evidence of a
                    # rollback. Reconcile this exact server-generated ID;
                    # never call AI again or create a second score here.
                    try:
                        rows = await repo._rows("GET", "job_scores", filters={"id": "eq." + score_id})
                        score_row = rows[0] if rows else None
                    except Exception:
                        raise HTTPException(503, detail={
                            "code": "ranking_save_uncertain", "model_run_id": model_run["id"],
                            "score_id": score_id,
                            "message": "The ranking save could not be confirmed. Refresh the job before requesting another AI ranking.",
                        }) from None
                if score_row is not None:
                    # The score is already durable. Never report it as absent or
                    # encourage another billable generation just because an
                    # ancillary status/audit update failed. Preserve provenance
                    # and expose the incomplete synchronization explicitly.
                    try:
                        saved = first_row(await jobs_with_scores(repo, str(job_id)))
                    except Exception:
                        raise HTTPException(503, detail={
                            "code": "ranking_saved_refresh_required", "model_run_id": model_run["id"],
                            "score_id": score_id,
                            "message": "Your ranking is saved. Its display could not be refreshed; refresh the job instead of generating again.",
                        }) from None
                    return {**saved, "operation_status": "saved_sync_pending",
                            "warnings": ["Your ranking was saved, but its status or activity log could not be synchronized. Refresh before requesting another ranking."],
                            "model_run_id": model_run["id"]}
                await fail_run(repo, model_run, metadata=model_metadata(exc))
                if isinstance(exc, HTTPException):
                    raise
                if isinstance(exc, getattr(adapter, "MissingFactsError", ())):
                    await missing_facts(repo, str(job_id), exc, model_run)
                raise HTTPException(502, "Job ranking failed. No successful ranking was recorded.") from None

    @application.post("/api/mobile/chat")
    async def chat(body: ChatRequest, repo: MobileRepository = Depends(repository)) -> dict:
        async with application.state.limits.work(repo.user_id, "ai"):
            adapter = studio_module(application)
            context = await build_context(repo, adapter, job_id=str(body.job_id) if body.job_id else None, resume_id=str(body.resume_id) if body.resume_id else None)
            model_run = await start_run(repo, "answer_chat", context, str(body.resume_id) if body.resume_id else None, mode=body.mode)
            questions = []
            try:
                await reserve_ai_usage(repo, "chat")
                raw_result = await run_in_threadpool(adapter.answer_chat, context, body.message, body.mode, [turn.model_dump() for turn in body.history])
                metadata = model_metadata(raw_result)
                result = ChatResult.model_validate(raw_result)
                questions = await persist_questions(repo, str(body.job_id) if body.job_id else None, result.questions)
                await repo.update("model_runs", model_run["id"], {"status": "succeeded", "completed_at": timestamp(), "provider": metadata["provider"], "model_name": metadata["model_name"], "output_summary": {"question_ids": [row["id"] for row in questions], "evidence_count": len(result.evidence), "model_metadata": metadata}})
                return {"reply": result.reply, "evidence": result.evidence, "questions": questions}
            except Exception as exc:
                # Keep confirmed/pending questions; never delete a reused row
                # because a later audit write failed.
                await fail_run(repo, model_run)
                if isinstance(exc, HTTPException):
                    raise
                if isinstance(exc, getattr(adapter, "MissingFactsError", ())):
                    await missing_facts(repo, str(body.job_id) if body.job_id else None, exc, model_run)
                raise HTTPException(502, "The AI response could not be completed. Please retry later.") from None

    return application


app = create_app()
