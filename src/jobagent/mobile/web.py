"""Public website entrypoint. Never imports the founder server or its SQLite data.

Run one process: ``uvicorn jobagent.mobile.web:app --workers 1``. Authentication,
RLS, invitations and spending controls are the same as the isolated tenant API.
Only the explicitly enumerated public build files are served, not the repository.
"""
from pathlib import Path
import os

from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .app import create_app as create_api


def trust_page_enabled(environ=None) -> bool:
    """Default off. Operators must set MOBILE_TRUST_PAGE_ENABLED to serve /beta/trust."""
    source = environ if environ is not None else os.environ
    return str(source.get("MOBILE_TRUST_PAGE_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}


def create_web_app(dist=None, **api_options):
    root = Path(dist or os.environ.get("MOBILE_WEB_DIST", "/app/web/dist")).resolve()

    def extra_ready():
        if not (root / "index.html").is_file() or not (root / "assets").is_dir():
            raise ValueError("Website build is unavailable.")

    application = create_api(extra_ready=extra_ready, **api_options)

    @application.get("/healthz")
    async def health():
        # Liveness does not claim database/provider availability.
        return {"status": "ok", "service": "job-pursuit-web"}

    @application.get("/")
    async def home():
        return RedirectResponse("/beta", status_code=307)

    async def workspace():
        index = root / "index.html"
        if not index.is_file():
            return JSONResponse({"detail": "Website build is unavailable."}, status_code=503)
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-store"})

    application.add_api_route("/beta", workspace, methods=["GET", "HEAD"])
    application.add_api_route("/beta/", workspace, methods=["GET", "HEAD"])
    if trust_page_enabled():
        application.add_api_route("/beta/trust", workspace, methods=["GET", "HEAD"])
        application.add_api_route("/beta/trust/", workspace, methods=["GET", "HEAD"])

        @application.get("/trust")
        async def trust_alias():
            return RedirectResponse("/beta/trust", status_code=307)

    # StaticFiles rejects traversal and does not follow directory symlinks.
    for name in ("assets", "brand"):
        if (root / name).is_dir():
            application.mount("/" + name, StaticFiles(directory=root / name, follow_symlink=False), name=name)

    def add_public_file(name):
        async def public_file():
            candidate = root / name
            if not candidate.is_file() or candidate.resolve().parent != root:
                return JSONResponse({"detail": "Not found."}, status_code=404)
            return FileResponse(candidate)
        application.add_api_route("/" + name, public_file, methods=["GET", "HEAD"])

    for name in ("favicon.svg", "icons.svg", "manifest.webmanifest", "apple-touch-icon.png", "icon-192.png", "icon-512.png"):
        add_public_file(name)
    # No SPA catch-all: /admin, /api/jobs, .env, source maps outside the public
    # build and unknown API endpoints must fail, never render a founder page.
    return application


app = create_web_app()
