"""Bounded operational events without request bodies, URLs, identities or secrets."""
import logging
import time
from uuid import uuid4

logger = logging.getLogger("jobagent.mobile.workflow")
_OPERATIONS = {
    "/api/mobile/discovery/search": "discovery", "/api/mobile/bootstrap": "bootstrap",
    "/api/mobile/resumes": "resume_upload", "/api/mobile/resumes/{resume_id}/text": "resume_parse",
    "/api/mobile/jobs/{job_id}/rank": "assessment", "/api/mobile/jobs/{job_id}/prepare": "document_generation",
    "/api/mobile/chat": "chat", "/api/mobile/billing/webhook": "billing_webhook",
    "/api/mobile/billing/checkout": "billing_checkout", "/api/mobile/billing/portal": "billing_portal",
    "/api/mobile/account/exports": "privacy_export", "/api/mobile/account/erasure": "privacy_erasure",
    "/api/mobile/artifacts/{artifact_id}/download": "document_download",
}


class WorkflowTelemetry:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        scope["jobpursuit_request_id"] = request_id
        started, status = time.monotonic(), 500
        async def traced_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = list(message.get("headers", [])) + [(b"x-request-id", request_id.encode())]
            await send(message)
        try:
            await self.app(scope, receive, traced_send)
        finally:
            # The matched server route, not the caller's path/query. Unknown
            # paths are collapsed to one label to avoid arbitrary log injection.
            route = getattr(scope.get("route"), "path", "unmatched")
            if len(route) > 160:
                route = "unmatched"
            if route not in ("/healthz", "/readyz", "/api/mobile/health", "/api/mobile/version") or status >= 400:
                method = scope.get("method", "")
                logger.log(logging.WARNING if status >= 400 else logging.INFO,
                           "workflow_request operation=%s method=%s status=%s duration_ms=%s request_id=%s",
                           _OPERATIONS.get(route, "workspace"),
                           method if method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS") else "OTHER",
                           status, min(86400000, round((time.monotonic() - started) * 1000)), request_id)
