"""Independent owner authorization for the founder listener, not beta/mobile auth.

Cloudflare Access and tunnel containment must remain enabled. No identity header,
cookie, proxy header, or loopback address alone establishes owner identity.
"""
from __future__ import annotations

import hmac
import ipaddress
import math
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

try:
    import jwt
except ImportError:  # Fail closed even on an incompletely provisioned deployment.
    jwt = None


CORS_METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS")
CORS_HEADERS = ("Authorization", "Content-Type", "Cf-Access-Jwt-Assertion")


@dataclass(frozen=True)
class FounderAuthSettings:
    mode: str = "cloudflare"
    issuer: str = ""
    audience: str = ""
    owner_email: str = ""
    allowed_origins: tuple[str, ...] = ()
    dev_token: str = ""

    @classmethod
    def from_environment(cls):
        return cls(
            mode=os.getenv("FOUNDER_AUTH_MODE", "cloudflare").strip(),
            issuer=os.getenv("FOUNDER_ACCESS_ISSUER", "").strip().rstrip("/"),
            audience=os.getenv("FOUNDER_ACCESS_AUDIENCE", "").strip(),
            owner_email=os.getenv("FOUNDER_OWNER_EMAIL", "").strip().casefold(),
            allowed_origins=tuple(origin.strip() for origin in
                                  os.getenv("FOUNDER_ALLOWED_ORIGINS", "").split(",") if origin.strip()),
            dev_token=os.getenv("FOUNDER_DEV_TOKEN", ""),
        )

    def configured(self) -> bool:
        if any(not _valid_origin(origin) for origin in self.allowed_origins):
            return False
        if self.mode == "loopback-dev":
            return len(self.dev_token) >= 32 and all(
                _loopback(urlsplit(origin).hostname or "") for origin in self.allowed_origins
            )
        return bool(
            self.mode == "cloudflare"
            and re.fullmatch(r"https://[a-z0-9-]+\.cloudflareaccess\.com", self.issuer)
            and self.audience and "*" not in self.audience
            and re.fullmatch(r"[^\s@,]+@[^\s@,]+", self.owner_email)
            and "*" not in self.owner_email
            and all(origin.startswith("https://") for origin in self.allowed_origins)
        )


def _valid_origin(origin: str) -> bool:
    try:
        parts = urlsplit(origin)
        return bool(parts.scheme in {"http", "https"} and parts.hostname and parts.port != 0
                    and not (parts.username or parts.password or parts.path or parts.query or parts.fragment)
                    and "*" not in origin)
    except ValueError:
        return False


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_founder_path(path: str) -> bool:
    # This app does not mount mobile. Even /api/mobile lookalikes on this listener
    # stay protected; the separately authenticated mobile listener is unchanged.
    return (path == "/api" or path.startswith("/api/") or path.startswith("/admin")
            or path == "/openapi.json" or path.startswith(("/docs", "/redoc")))


class FounderAuthError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


class FounderTokenVerifier:
    def __init__(self, settings: FounderAuthSettings):
        self.settings = settings
        self.jwks = None
        if jwt is not None and settings.mode == "cloudflare" and settings.configured():
            # The key endpoint is derived ONLY from trusted operator configuration.
            self.jwks = jwt.PyJWKClient(settings.issuer + "/cdn-cgi/access/certs", timeout=5,
                                        cache_jwk_set=True, lifespan=300)

    def verify(self, token: str) -> None:
        if self.jwks is None or jwt is None:
            raise FounderAuthError(503, "Founder authentication is not configured.")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise ValueError("Invalid token header")
            key = self.jwks.get_signing_key_from_jwt(token).key
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
            if not isinstance(key, RSAPublicKey):
                raise ValueError("Invalid signing key type")
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.settings.issuer,
                audience=self.settings.audience,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "email", "type"]},
            )
            # Exact comparisons supplement the library's claim validation.
            if claims["iss"] != self.settings.issuer or claims["type"] != "app":
                raise ValueError("Wrong token context")
            if not isinstance(claims["sub"], str) or not claims["sub"].strip():
                raise ValueError("Missing subject")
            for name in ("exp", "iat", "nbf"):
                if name in claims and (type(claims[name]) not in (int, float) or not math.isfinite(claims[name])):
                    raise ValueError("Invalid timestamp")
            if not isinstance(claims["email"], str):
                raise ValueError("Missing identity")
        except Exception as exc:
            # Never echo tokens, JWKS responses, upstream diagnostics or identity.
            raise FounderAuthError(401, "Invalid founder access token.") from exc
        if claims["email"].strip().casefold() != self.settings.owner_email.casefold():
            raise FounderAuthError(403, "Founder owner access required.")


class FounderAuthMiddleware:
    def __init__(self, app, settings: FounderAuthSettings):
        self.app, self.settings = app, settings
        self.verifier = FounderTokenVerifier(settings)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or not is_founder_path(scope["path"]):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        headers = Headers(scope=scope)
        try:
            if not self.settings.configured():
                raise FounderAuthError(503, "Founder authentication is not configured.")
            origin = headers.get("origin")
            if (len(headers.getlist("origin")) > 1
                    or (origin is not None and origin not in self.settings.allowed_origins)
                    or (origin is None and headers.get("sec-fetch-site") == "cross-site")):
                raise FounderAuthError(403, "Founder request origin is not allowed.")
            preflight = (scope["method"] == "OPTIONS" and origin is not None
                         and headers.get("access-control-request-method") in CORS_METHODS)
            if not preflight:
                token = self._token(headers)
                if self.settings.mode == "loopback-dev":
                    peer = (scope.get("client") or ("",))[0]
                    host = urlsplit("http://" + headers.get("host", "")).hostname or ""
                    proxied = any(name.startswith(("cf-", "x-forwarded-")) or name == "forwarded"
                                  for name in headers)
                    if (not _loopback(peer) or not _loopback(host) or proxied
                            or not hmac.compare_digest(token.encode(), self.settings.dev_token.encode())):
                        raise FounderAuthError(401, "Local development authentication required.")
                else:
                    await run_in_threadpool(self.verifier.verify, token)
        except (ValueError, FounderAuthError) as exc:
            error = exc if isinstance(exc, FounderAuthError) else FounderAuthError(401, "Invalid founder request.")
            return await JSONResponse({"detail": error.detail}, status_code=error.status,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        return await self.app(scope, receive, private_send)

    @staticmethod
    def _token(headers: Headers) -> str:
        if any(len(headers.getlist(name)) > 1 for name in ("authorization", "cf-access-jwt-assertion")):
            raise FounderAuthError(401, "Ambiguous founder credentials.")
        assertion = headers.get("cf-access-jwt-assertion", "")
        authorization = headers.get("authorization", "")
        bearer = ""
        if authorization:
            scheme, _, bearer = authorization.partition(" ")
            if scheme.lower() != "bearer" or not bearer or bearer.strip() != bearer:
                raise FounderAuthError(401, "Invalid founder credentials.")
        if assertion and bearer and assertion != bearer:
            raise FounderAuthError(401, "Ambiguous founder credentials.")
        token = assertion or bearer
        if not token or len(token) > 16384:
            raise FounderAuthError(401, "Founder access token required.")
        return token
