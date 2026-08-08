"""Fail-closed Supabase JWT authentication for beta API routes.

User requests are authenticated with a Supabase access token and the project's
public JWKS.  This module deliberately has no service-role key support: service
credentials belong only in separately audited server-to-server operations.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import UUID

import httpx
import jwt
from fastapi import Header, HTTPException, Request, status
from jwt import PyJWKClient


_ALLOWED_ALGORITHMS = ("RS256", "ES256")
_JWKS_CACHE_TTL_SECONDS = 300
_JWKS_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class BetaAuthSettings:
    """Public Supabase authentication settings required by the beta API."""

    supabase_url: str
    jwt_issuer: str
    jwt_audience: str

    @property
    def jwks_url(self) -> str:
        return "%s/auth/v1/.well-known/jwks.json" % self.supabase_url


@dataclass(frozen=True)
class AuthenticatedUser:
    """Minimal immutable identity derived from a verified access token."""

    id: UUID
    email: Optional[str]
    role: str


class _JwksClientCache:
    """Small TTL cache so each request does not make a network call for JWKS."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: Dict[str, tuple[PyJWKClient, float]] = {}

    def get(self, jwks_url: str) -> PyJWKClient:
        now = time.monotonic()
        with self._lock:
            cached = self._clients.get(jwks_url)
            if cached is not None and now - cached[1] < _JWKS_CACHE_TTL_SECONDS:
                return cached[0]

            client = PyJWKClient(
                jwks_url,
                cache_keys=True,
                lifespan=_JWKS_CACHE_TTL_SECONDS,
                timeout=_JWKS_TIMEOUT_SECONDS,
            )
            self._clients[jwks_url] = (client, now)
            return client


_jwks_clients = _JwksClientCache()


def get_beta_auth_settings() -> BetaAuthSettings:
    """Load beta auth configuration, refusing partial or unsafe configuration."""

    supabase_url = os.getenv("BETA_SUPABASE_URL", "").rstrip("/")
    jwt_issuer = os.getenv("BETA_SUPABASE_JWT_ISSUER", "").rstrip("/")
    jwt_audience = os.getenv("BETA_SUPABASE_JWT_AUDIENCE", "authenticated")

    if not supabase_url.startswith("https://") or not jwt_issuer.startswith("https://") or not jwt_audience:
        raise RuntimeError("Beta authentication is not configured")

    expected_issuer = "%s/auth/v1" % supabase_url
    if jwt_issuer != expected_issuer:
        raise RuntimeError("Beta authentication issuer does not match Supabase URL")

    return BetaAuthSettings(
        supabase_url=supabase_url,
        jwt_issuer=jwt_issuer,
        jwt_audience=jwt_audience,
    )


def _auth_error(detail: str = "Invalid or expired authentication token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _service_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Beta authentication is unavailable",
    )


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise _auth_error("Missing bearer token")

    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token or " " in token:
        raise _auth_error("Invalid bearer token")
    return token


def _decode_access_token(token: str, settings: BetaAuthSettings) -> Dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in _ALLOWED_ALGORITHMS or not isinstance(key_id, str) or not key_id:
            raise _auth_error()

        signing_key = _jwks_clients.get(settings.jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "role"]},
        )
    except HTTPException:
        raise
    except (jwt.PyJWTError, httpx.HTTPError, ValueError, OSError):
        # Do not expose provider, key, or token details to callers.
        raise _auth_error()


def _to_authenticated_user(claims: Dict[str, Any]) -> AuthenticatedUser:
    try:
        user_id = UUID(str(claims["sub"]))
        role = claims["role"]
        email = claims.get("email")
    except (KeyError, TypeError, ValueError):
        raise _auth_error()

    if role != "authenticated" or (email is not None and not isinstance(email, str)):
        raise _auth_error()

    return AuthenticatedUser(id=user_id, email=email, role=role)


async def get_authenticated_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> AuthenticatedUser:
    """FastAPI dependency for routes which require a verified Supabase user.

    ``request`` is intentionally accepted to make the dependency usable with
    FastAPI route injection and future request-scoped audit metadata.  No
    request data, headers, or token values are logged or persisted here.
    """

    del request
    try:
        settings = get_beta_auth_settings()
    except RuntimeError:
        raise _service_unavailable()

    token = _extract_bearer_token(authorization)
    return _to_authenticated_user(_decode_access_token(token, settings))
