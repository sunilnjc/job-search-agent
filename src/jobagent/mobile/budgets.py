"""Durable, fail-closed mobile entitlements and AI reservations (JP-TEST-020).

App contract (no wiring or provider calls in this module)::

    # One limiter per app; before token validation / upstream /auth/v1/user:
    pre_auth_limiter.check(request)
    # After verified Auth AND the existing private verified-email gate:
    await require_mobile_access(repo)
    # Immediately before each billed workflow, after local validation:
    reservation = await reserve_ai_usage(repo, "prepare")  # or chat / rank

The repository's client MUST carry the authenticated caller's bearer and a
publishable key. The RPC derives identity from auth.uid(), not repo.user_id or
user metadata. Neither quotas nor costs are accepted from the caller. The
operation is a route-owned literal, never a request/model-selected value.

One reservation covers one current studio workflow (one provider request, SDK
retries disabled). If workflows gain retries/fallbacks/multiple calls, reserve
before EACH call or provision an audited worst-case workflow cost. Costs are
admin-configured conservative budget units, not provider-reconciled invoices;
operators must bound model/input/output costs when provisioning the unit price.

Reservations are never refunded here, including provider errors, cancellation,
lost RPC acknowledgements, or worker crashes. No automatic retry or reused
reservation authorizes work. Thus uncertainty sacrifices availability, not the
spending boundary. Supabase is authoritative; there is no local/env fallback.
Tests may explicitly inject a transport into the repository's HTTP client.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import deque
from typing import Any, Callable, Dict
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request

from .repository import MobileRepository


_OPERATIONS = frozenset({"chat", "rank", "prepare"})
_MAX_RESPONSE_BYTES = 16 * 1024
_UNAVAILABLE = (
    "Usage controls are unavailable. Ask the operator to apply "
    "0007_mobile_usage_controls.sql, reload the Supabase API schema cache, "
    "and provision membership and AI quota before retrying. No AI work was started."
)


def _retry(value: Any, default: int = 60, maximum: int = 86400) -> str:
    # Do not forward arbitrary upstream headers or accept booleans as integers.
    if type(value) is int:
        return str(min(max(value, 1), maximum))
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 9:
        return str(min(max(int(value), 1), maximum))
    return str(default)


def _unavailable() -> HTTPException:
    return HTTPException(503, _UNAVAILABLE, headers={"Retry-After": "60"})


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response key")
        result[key] = value
    return result


async def _rpc(repo: MobileRepository, name: str, payload: dict) -> Dict[str, Any]:
    """Bounded transport; never use the generic 404-as-missing-record path."""
    try:
        async with repo.client.stream(
            "POST", "/rest/v1/rpc/" + name, json=payload, follow_redirects=False,
        ) as response:
            if response.status_code in (401, 403):
                raise HTTPException(response.status_code, "Session is invalid or usage access is denied.")
            if response.status_code == 429:
                raise HTTPException(429, "Usage service is busy. Retry later.", headers={
                    "Retry-After": _retry(response.headers.get("Retry-After"), maximum=3600),
                })
            if not 200 <= response.status_code < 300:
                # PGRST202/205, missing relation/function, permissions, and
                # transient failures are all non-authorizations, never bypasses.
                raise _unavailable()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > _MAX_RESPONSE_BYTES:
                    raise _unavailable()
                raw.extend(chunk)
        result = json.loads(raw, object_pairs_hook=_unique_object)
    except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
        raise _unavailable() from None
    if not isinstance(result, dict) or type(result.get("allowed")) is not bool:
        raise _unavailable()
    if not result["allowed"]:
        code = result.get("code")
        if code == "not_entitled":
            raise HTTPException(403, "An active administrator-approved membership is required.")
        if code == "quota_unprovisioned":
            raise HTTPException(403, "AI quota is not provisioned or has expired. Contact the operator.")
        if code == "quota_exhausted":
            raise HTTPException(429, "Your AI budget is exhausted. Retry after the budget resets or contact the operator.",
                                headers={"Retry-After": _retry(result.get("retry_after"))})
        if code == "duplicate_reservation":
            raise HTTPException(409, "This AI reservation was already used. No additional AI work was started.")
        raise _unavailable()
    if result.get("user_id") != repo.user_id:
        raise _unavailable()
    return result


async def require_mobile_access(repo: MobileRepository) -> None:
    """No caching: revocations must be observed by subsequent API requests."""
    await _rpc(repo, "mobile_check_access", {})


async def reserve_ai_usage(repo: MobileRepository, operation: str) -> Dict[str, Any]:
    """Charge durable quota before work; raises rather than failing open.

    No request IDs supplied by clients, caller cost estimates, successful replay,
    or implicit retry. Do not start a provider unless this call returns normally.
    An acknowledgement lost after commit consumes quota without authorizing work.
    """
    if not isinstance(operation, str) or operation not in _OPERATIONS:
        raise ValueError("Use a route-owned chat, rank, or prepare operation")
    reservation_id = str(uuid4())
    result = await _rpc(repo, "mobile_reserve_ai_usage", {
        "p_operation": operation, "p_reservation_id": reservation_id,
    })
    if (result.get("reservation_id") != reservation_id or result.get("operation") != operation
            or any(type(result.get(key)) is not int or result[key] < 0 for key in (
                "reserved_units", "remaining_period_units", "remaining_daily_units"))
            or result["reserved_units"] == 0):
        raise _unavailable()
    return result


class PreAuthLimiter:
    """Bound upstream Auth attempts before identity exists; NOT a spending cap.

    Sliding windows bound both a peer and the process, including rotating bad
    bearer strings. Ignore all forwarding headers: use only request.client.host
    as supplied by the deployment's trusted ASGI/proxy configuration. If the
    edge rewrites this value, configure trusted proxies there, never trust client
    X-Forwarded-For here. The gateway must add shared multi-worker/IP limits.
    The app may inject per_peer=120,total=1200 per minute for UI startup and
    review/download bursts; that does not change the durable AI budget or the
    separate authenticated request limit. When a reverse proxy collapses all
    clients to one address they share the peer cap: configure trusted proxy
    forwarding and a shared edge limiter before scaling, not an arbitrary-header
    bypass. The default here remains 30/300 per minute.
    Active peer keys are never evicted to admit attackers; capacity fails closed.
    Only bounded peer hashes/counters are retained, never tokens or raw IPs.
    """

    def __init__(self, *, per_peer: int = 30, total: int = 300,
                 window_seconds: float = 60, max_peers: int = 4096,
                 clock: Callable[[], float] = time.monotonic):
        if (any(type(value) is not int or value <= 0 for value in (per_peer, total, max_peers))
                or type(window_seconds) not in (int, float) or not math.isfinite(window_seconds)
                or window_seconds <= 0):
            raise ValueError("Positive finite pre-auth limits are required")
        self.per_peer, self.total, self.window_seconds, self.max_peers = per_peer, total, window_seconds, max_peers
        self._clock = clock
        self._peers: Dict[str, deque] = {}
        self._global: deque = deque()
        self._lock = threading.Lock()

    def check(self, request: Request) -> None:
        peer = request.client.host if request.client else "unknown"
        peer = peer if isinstance(peer, str) and len(peer) <= 256 else "unknown"
        key = hashlib.sha256(peer.encode("utf-8", errors="replace")).hexdigest()
        with self._lock:
            now = self._clock()
            cutoff = now - self.window_seconds
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            if len(self._global) >= self.total:
                self._deny(self._global[0] + self.window_seconds - now)
            if key not in self._peers:
                for stale in [k for k, events in self._peers.items() if events[-1] <= cutoff]:
                    del self._peers[stale]
                if len(self._peers) >= self.max_peers:
                    self._deny(self.window_seconds)
                self._peers[key] = deque()
            events = self._peers[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.per_peer:
                self._deny(events[0] + self.window_seconds - now)
            events.append(now)
            self._global.append(now)

    @staticmethod
    def _deny(delay: float) -> None:
        raise HTTPException(429, "Too many session checks. Please retry shortly.",
                            headers={"Retry-After": str(max(1, math.ceil(delay)))})
