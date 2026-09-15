"""Provider-neutral billing boundary. No provider selection or network on import.

Checkout/portal take a remotely verified repository identity. Erasure takes ONLY
trusted UUIDs from the durable account-worker claim, never a public route body.
Stripe test mode is an explicitly documented implementation inference, not a
commercial/provider decision. Checkout defaults OFF. No environment switch can
substitute a fake or enable live payments. Import/configuration performs no I/O.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Protocol, Tuple
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException

from .repository import _jwt_role

MAX_WEBHOOK_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_UNITS = 9_000_000_000_000_000
BILLING_TABLES = ("mobile_billing_accounts", "mobile_billing_events", "mobile_billing_operations")
BILLING_EXPORT_VIEW = "mobile_billing_export"
BILLING_EXPORT_COLUMNS = ("account_id", "user_id", "provider", "mode", "status", "plan_key",
                          "period_start", "paid_through", "cancel_at_period_end", "cancellation_status", "updated_at")


def unavailable() -> HTTPException:
    return HTTPException(503, "Billing is unavailable. Ask the operator to review provider configuration, "
                         "apply 0009_billing.sql and reconcile pending billing work before retrying.",
                         headers={"Retry-After": "30"})


def _uuid(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A trusted UUID is required") from None


def _json(raw: bytes) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous JSON")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _https(value: str, hosts: Optional[Tuple[str, ...]] = None) -> bool:
    try:
        parts = urlsplit(value)
        return (isinstance(value, str) and len(value) <= 4096 and parts.scheme == "https"
                and bool(parts.hostname) and not parts.username and not parts.password
                and parts.port in (None, 443) and not re.search(r"[\s\x00-\x1f]", value)
                and (hosts is None or parts.hostname in hosts))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Plan:
    """Server catalog entry; units are conservative caps, not commercial prices."""
    price_id: str
    period_limit: int
    daily_limit: int

    def __post_init__(self):
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", self.price_id)
                or any(type(value) is not int or not 0 < value <= MAX_UNITS
                       for value in (self.period_limit, self.daily_limit))):
            raise ValueError("Invalid server billing plan")


@dataclass(frozen=True)
class BillingSettings:
    provider: str = ""
    mode: str = "test"
    checkout_enabled: bool = False
    plans: Mapping[str, Plan] = field(default_factory=dict)
    success_url: str = ""
    cancel_url: str = ""
    portal_return_url: str = ""

    def __post_init__(self):
        if (self.mode != "test" or type(self.checkout_enabled) is not bool
                or (self.provider and not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", self.provider))
                or len(self.plans) > 50 or len({plan.price_id for plan in self.plans.values()}) != len(self.plans)
                or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", key) for key in self.plans)):
            raise ValueError("Invalid billing configuration; live billing is not implemented")
        if self.checkout_enabled and (not self.provider or not self.plans or
                not all(_https(url) for url in (self.success_url, self.cancel_url, self.portal_return_url))):
            raise ValueError("Checkout needs a complete reviewed server configuration")

    @classmethod
    def from_env(cls):
        """Read only explicit billing variables, never dotenv or founder config."""
        try:
            raw = os.environ.get("MOBILE_BILLING_PLANS", "{}")
            if len(raw) > 16384:
                raise ValueError
            catalog = _json(raw.encode())
            if not isinstance(catalog, dict):
                raise ValueError
            enabled = os.environ.get("MOBILE_BILLING_CHECKOUT_ENABLED", "false")
            if enabled not in ("true", "false"):
                raise ValueError
            return cls(provider=os.environ.get("MOBILE_BILLING_PROVIDER", ""),
                       mode=os.environ.get("MOBILE_BILLING_MODE", "test"), checkout_enabled=enabled == "true",
                       plans={key: Plan(**value) for key, value in catalog.items()},
                       success_url=os.environ.get("MOBILE_BILLING_SUCCESS_URL", ""),
                       cancel_url=os.environ.get("MOBILE_BILLING_CANCEL_URL", ""),
                       portal_return_url=os.environ.get("MOBILE_BILLING_PORTAL_RETURN_URL", ""))
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise unavailable() from None


@dataclass(frozen=True)
class WebhookEvent:
    id: str
    customer_id: str
    body_sha256: str


@dataclass(frozen=True)
class SubscriptionSnapshot:
    """Only produced by an authoritative provider GET, NEVER a webhook payload.

    paid_through must be established by a settled invoice for this subscription
    and current recurring price/period, not checkout completion or status alone.
    Any unpaid/ambiguous/multi-subscription state must normalize to ineligible.
    """
    customer_id: str
    subscription_id: Optional[str]
    status: str
    price_id: Optional[str]
    period_start: Optional[int]
    period_end: Optional[int]
    paid_through: Optional[int]
    paid: bool = False
    cancel_at_period_end: bool = False


@dataclass(frozen=True)
class CancellationReceipt:
    """Refetched provider proof; cancellation-request acknowledgement is not proof.

    Adapter must resolve every pending idempotency intent and confirm no active
    subscription, open checkout, or future automatic collection. Uncertain/aged
    intents are a blocker, not an empty list. No charge/refund/proration allowed.
    """
    customer_id: Optional[str]
    cancellation_confirmed: bool
    no_future_collection: bool
    open_subscription_ids: Tuple[str, ...] = ()
    open_checkout_ids: Tuple[str, ...] = ()
    unresolved_operation_ids: Tuple[str, ...] = ()


class BillingProvider(Protocol):
    """Reviewed server adapter contract; never selected by request parameters.

    Implementations pin HTTPS origin/API version, use PinnedProviderHTTP, disable
    redirects/proxies/retries, and have no tool to create prices, charge or refund.
    Customer/checkout creations use the durable operation UUID as idempotency key;
    retry only the identical operation inside the provider retention window.
    Fetches and cancellation must cover ALL customer subscriptions/checkouts,
    rejecting pagination/ambiguity rather than treating omissions as absence.
    """
    name: str
    mode: str
    checkout_hosts: Tuple[str, ...]
    portal_hosts: Tuple[str, ...]

    def verify_webhook(self, raw: bytes, signature: str, now: int) -> Optional[WebhookEvent]: ...
    async def create_customer(self, user_id: str, verified_email: str, operation: dict) -> str: ...
    async def create_checkout(self, customer_id: str, user_id: str, plan: Plan,
                              settings: BillingSettings, operation: dict) -> dict: ...
    async def create_portal(self, customer_id: str, return_url: str) -> dict: ...
    async def fetch_subscription(self, customer_id: str) -> SubscriptionSnapshot: ...
    async def cancel_for_erasure(self, account: dict, operations: list, request_id: str) -> CancellationReceipt: ...


class PinnedProviderHTTP:
    """Transport for a reviewed adapter's CODE-owned origin/version/route set.

    Never feed origin, allowed_routes, headers, or URLs from a client/webhook.
    Secrets deliberately have no repr, logging, or exception-body exposure.
    A concrete adapter must reject live keys and cross-mode response objects.
    """
    def __init__(self, *, origin: str, headers: Mapping[str, str],
                 allowed_routes: Tuple[Tuple[str, str], ...], transport=None):
        if (not _https(origin) or urlsplit(origin).path not in ("", "/")
                or urlsplit(origin).query or urlsplit(origin).fragment):
            raise ValueError("Invalid pinned billing origin")
        self._origin, self._headers, self._routes, self._transport = origin.rstrip("/"), dict(headers), allowed_routes, transport

    async def request(self, method: str, path: str, *, data=None, params=None, idempotency_key=None) -> dict:
        if (not isinstance(path, str) or ".." in path or any(c in path for c in "%?#\\")
                or not any(method == verb and re.fullmatch(pattern, path) for verb, pattern in self._routes)):
            raise unavailable()
        headers = dict(self._headers)
        if idempotency_key:
            headers["Idempotency-Key"] = _uuid(idempotency_key)
        async with httpx.AsyncClient(base_url=self._origin, headers=headers, transport=self._transport,
                follow_redirects=False, trust_env=False, timeout=httpx.Timeout(20, connect=5)) as client:
            return await _bounded_request(client, method, path, data=data, params=params)


async def _bounded_request(client, method, path, **kwargs) -> dict:
    try:
        async with client.stream(method, path, **kwargs) as response:
            if not 200 <= response.status_code < 300:
                raise unavailable()
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise unavailable()
                raw.extend(chunk)
        body = _json(raw)
        if not isinstance(body, dict):
            raise ValueError
        return body
    except (httpx.HTTPError, ValueError, TypeError, RecursionError):
        raise unavailable() from None


def verify_stripe_signature(raw: bytes, signature: str, secret: str, *, now: Optional[int] = None) -> dict:
    """Offline-compatible raw-body verifier, NOT a selected Stripe adapter.

    Primary source: https://docs.stripe.com/webhooks#verify-manually
    Bound both old and future timestamps to 300s; persist event IDs separately.
    """
    try:
        if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_WEBHOOK_BYTES
                or not isinstance(signature, str) or len(signature) > 4096
                or not isinstance(secret, str) or not secret.startswith("whsec_") or len(secret) > 1024):
            raise ValueError
        parts = [part.strip().split("=", 1) for part in signature.split(",")]
        stamps = [value for key, value in parts if key == "t"]
        signatures = [value for key, value in parts if key == "v1"]
        if len(stamps) != 1 or not re.fullmatch(r"[0-9]{1,12}", stamps[0]) or not 1 <= len(signatures) <= 8:
            raise ValueError
        timestamp = int(stamps[0])
        if abs((int(time.time()) if now is None else now) - timestamp) > 300:
            raise ValueError
        expected = hmac.new(secret.encode(), stamps[0].encode() + b"." + raw, hashlib.sha256).hexdigest()
        matches = [hmac.compare_digest(expected, value) for value in signatures
                   if re.fullmatch(r"[0-9a-f]{64}", value)]
        if not any(matches):
            raise ValueError
        body = _json(raw)
        if not isinstance(body, dict):
            raise ValueError
        return body
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(400, "Invalid billing webhook signature or payload.") from None


class StripeTestProvider:
    """Pinned, test-only, card/one-licensed-price adapter. No live-mode path.

    No automatic retries, customer search, metadata-based membership, refunds,
    invoice payments, or price/account creation. Unsupported billing structures
    fail closed. All calls can use an explicitly injected httpx.MockTransport.
    """
    name, mode = "stripe", "test"
    api_version = "2024-06-20"
    checkout_hosts, portal_hosts = ("checkout.stripe.com",), ("billing.stripe.com",)
    terminal = ("canceled", "incomplete_expired")

    def __init__(self, secret_key: str, webhook_secret: str, *, portal_configuration: str = "",
                 transport=None, clock=time.time):
        if (not isinstance(secret_key, str) or not re.fullmatch(r"sk_test_[A-Za-z0-9]{8,512}", secret_key)
                or not isinstance(webhook_secret, str) or not re.fullmatch(r"whsec_[A-Za-z0-9]{8,512}", webhook_secret)
                or (portal_configuration and not re.fullmatch(r"bpc_[A-Za-z0-9]{1,160}", portal_configuration))):
            raise unavailable()
        self._webhook_secret, self._portal_configuration, self.clock = webhook_secret, portal_configuration, clock
        self.http = PinnedProviderHTTP(origin="https://api.stripe.com", transport=transport,
            headers={"Authorization": "Bearer " + secret_key, "Stripe-Version": self.api_version},
            allowed_routes=(("POST", r"/v1/customers"), ("GET", r"/v1/customers/cus_[A-Za-z0-9]+"),
                ("POST", r"/v1/checkout/sessions"), ("GET", r"/v1/checkout/sessions"),
                ("GET", r"/v1/checkout/sessions/cs_test_[A-Za-z0-9]+"),
                ("POST", r"/v1/checkout/sessions/cs_test_[A-Za-z0-9]+/expire"),
                ("GET", r"/v1/billing_portal/configurations/bpc_[A-Za-z0-9]+"),
                ("POST", r"/v1/billing_portal/sessions"),
                ("GET", r"/v1/subscriptions"), ("GET", r"/v1/subscriptions/sub_[A-Za-z0-9]+"),
                ("DELETE", r"/v1/subscriptions/sub_[A-Za-z0-9]+"),
                ("GET", r"/v1/invoices"), ("GET", r"/v1/invoices/in_[A-Za-z0-9]+"),
                ("GET", r"/v1/charges/ch_[A-Za-z0-9]+"),
                ("GET", r"/v1/subscription_schedules"), ("GET", r"/v1/invoiceitems")))

    @classmethod
    def from_env(cls, *, transport=None):
        return cls(os.environ.get("MOBILE_BILLING_STRIPE_SECRET_KEY", ""),
                   os.environ.get("MOBILE_BILLING_STRIPE_WEBHOOK_SECRET", ""),
                   portal_configuration=os.environ.get("MOBILE_BILLING_STRIPE_PORTAL_CONFIGURATION", ""),
                   transport=transport)

    @staticmethod
    def _id(value, prefix):
        if not isinstance(value, str) or not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9]{1,160}", value):
            raise unavailable()
        return value

    @staticmethod
    def _object(value, kind, *, customer=None, object_id=None):
        if (not isinstance(value, dict) or value.get("object") != kind or value.get("livemode") is not False
                or (customer is not None and value.get("customer") != customer)
                or (object_id is not None and value.get("id") != object_id)):
            raise unavailable()
        return value

    def verify_webhook(self, raw, signature, now):
        body = verify_stripe_signature(raw, signature, self._webhook_secret, now=now)
        if (body.get("object") != "event" or body.get("livemode") is not False
                or body.get("api_version") != self.api_version or body.get("account") is not None):
            raise HTTPException(400, "A pinned-version, test-mode platform billing event is required.")
        event_type = body.get("type")
        accepted = {"customer.subscription." + action for action in
                    ("created", "updated", "deleted", "paused", "resumed", "pending_update_applied", "pending_update_expired")}
        accepted.update("invoice." + action for action in
                        ("paid", "payment_failed", "payment_action_required", "voided", "marked_uncollectible", "updated"))
        accepted.update(("checkout.session.completed", "checkout.session.expired", "charge.refunded", "charge.updated"))
        if not isinstance(event_type, str) or event_type not in accepted:
            return None
        obj = body.get("data", {}).get("object") if isinstance(body.get("data"), dict) else None
        if not isinstance(obj, dict) or not isinstance(obj.get("customer"), str):
            raise HTTPException(400, "Invalid billing event customer.")
        try:
            return WebhookEvent(self._id(body.get("id"), "evt_"), self._id(obj["customer"], "cus_"),
                                hashlib.sha256(raw).hexdigest())
        except HTTPException:
            raise HTTPException(400, "Invalid billing event identity.") from None

    @staticmethod
    def _operation(operation, clock):
        # Completed objects are retrieved, not recreated; uncertain requests can
        # retry only within the conservative retention window with the SAME key.
        operation_id = _uuid(operation["id"])
        if operation.get("state") == "pending":
            created = datetime.fromisoformat(operation["created_at"].replace("Z", "+00:00"))
            if created.tzinfo is None or not 0 <= clock() - created.timestamp() < 23 * 3600:
                raise unavailable()
        elif operation.get("state") != "complete":
            raise unavailable()
        return operation_id

    async def create_customer(self, user_id, verified_email, operation):
        operation_id = self._operation(operation, self.clock)
        if operation.get("state") == "complete":
            customer = self._id(operation.get("object_id"), "cus_")
            result = await self.http.request("GET", "/v1/customers/" + customer)
            self._object(result, "customer", object_id=customer)
        else:
            result = await self.http.request("POST", "/v1/customers", idempotency_key=operation_id,
                data={"email": verified_email, "metadata[user_id]": _uuid(user_id),
                      "metadata[operation_id]": operation_id})
            self._object(result, "customer")
        return self._id(result.get("id"), "cus_")

    async def create_checkout(self, customer_id, user_id, plan, settings, operation):
        customer_id = self._id(customer_id, "cus_")
        operation_id = self._operation(operation, self.clock)
        if operation.get("state") == "complete":
            session_id = self._id(operation.get("object_id"), "cs_test_")
            result = await self.http.request("GET", "/v1/checkout/sessions/" + session_id)
            self._object(result, "checkout.session", customer=customer_id, object_id=session_id)
        else:
            result = await self.http.request("POST", "/v1/checkout/sessions", idempotency_key=operation_id,
                data={"customer": customer_id, "mode": "subscription", "client_reference_id": _uuid(user_id),
                      "line_items[0][price]": self._id(plan.price_id, "price_"), "line_items[0][quantity]": "1",
                      "payment_method_types[0]": "card", "allow_promotion_codes": "false",
                      "success_url": settings.success_url, "cancel_url": settings.cancel_url,
                      "metadata[operation_id]": operation_id})
            self._object(result, "checkout.session", customer=customer_id)
        self._id(result.get("id"), "cs_test_")
        if result.get("mode") != "subscription" or result.get("status") != "open":
            raise unavailable()
        return result

    async def create_portal(self, customer_id, return_url):
        customer_id = self._id(customer_id, "cus_")
        configuration = self._id(self._portal_configuration, "bpc_")
        config = await self.http.request("GET", "/v1/billing_portal/configurations/" + configuration)
        self._object(config, "billing_portal.configuration", object_id=configuration)
        # No plan changes/prorations through this launch portal. Operator must
        # configure the portal in test mode; never silently use provider default.
        features = config.get("features", {})
        if (config.get("active") is not True or not isinstance(features, dict)
                or features.get("subscription_update", {}).get("enabled") is not False):
            raise unavailable()
        result = await self.http.request("POST", "/v1/billing_portal/sessions",
            data={"customer": customer_id, "configuration": configuration, "return_url": return_url})
        return self._object(result, "billing_portal.session", customer=customer_id)

    async def _list(self, path, customer, kind, **filters):
        customer = self._id(customer, "cus_")
        result, seen, after = [], set(), None
        for _ in range(10):
            params = {"customer": customer, "limit": "100", **filters}
            if after:
                params["starting_after"] = after
            page = await self.http.request("GET", path, params=params)
            if (page.get("object") != "list" or type(page.get("has_more")) is not bool
                    or not isinstance(page.get("data"), list) or len(page["data"]) > 100):
                raise unavailable()
            for item in page["data"]:
                self._object(item, kind, customer=customer)
                item_id = item.get("id")
                if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,200}", item_id) or item_id in seen:
                    raise unavailable()
                seen.add(item_id)
                result.append(item)
            if not page["has_more"]:
                return result
            if not page["data"]:
                raise unavailable()
            after = page["data"][-1]["id"]
        raise unavailable()  # Never interpret truncated pagination as absence.

    async def fetch_subscription(self, customer_id):
        subs = await self._list("/v1/subscriptions", customer_id, "subscription", status="all")
        current = [sub for sub in subs if sub.get("status") not in self.terminal]
        if len(current) != 1:
            return SubscriptionSnapshot(customer_id, None, "none" if not current else "ambiguous", None, None, None, None)
        sub_id = self._id(current[0]["id"], "sub_")
        sub = await self.http.request("GET", "/v1/subscriptions/" + sub_id)
        self._object(sub, "subscription", customer=customer_id, object_id=sub_id)
        denied = SubscriptionSnapshot(customer_id, sub_id, "ineligible", None, None, None, None)
        if sub.get("status") != "active" or sub.get("pause_collection") or sub.get("pending_update") or sub.get("schedule"):
            return denied
        items = sub.get("items", {})
        if items.get("has_more") is not False or not isinstance(items.get("data"), list) or len(items["data"]) != 1:
            return denied
        item = items["data"][0]
        price = item.get("price", {})
        if (item.get("quantity") != 1 or price.get("livemode") is not False or price.get("type") != "recurring"
                or price.get("recurring", {}).get("usage_type") != "licensed"):
            return denied
        price_id = self._id(price.get("id"), "price_")
        start, end = sub.get("current_period_start"), sub.get("current_period_end")
        if not all(type(value) is int and 0 < value < 4_102_444_800 for value in (start, end)) or end <= start:
            return denied
        if not sub.get("latest_invoice"):
            return denied
        invoice_id = self._id(sub["latest_invoice"], "in_")
        invoice = await self.http.request("GET", "/v1/invoices/" + invoice_id)
        self._object(invoice, "invoice", customer=customer_id, object_id=invoice_id)
        lines = invoice.get("lines", {})
        if (invoice.get("subscription") != sub_id or invoice.get("status") != "paid"
                or invoice.get("paid") is not True or invoice.get("paid_out_of_band") is not False
                or invoice.get("amount_remaining") != 0 or type(invoice.get("amount_paid")) is not int
                or invoice["amount_paid"] <= 0 or lines.get("has_more") is not False
                or not isinstance(lines.get("data"), list) or len(lines["data"]) != 1):
            return denied
        line = lines["data"][0]
        if (line.get("type") != "subscription" or line.get("subscription") != sub_id
                or line.get("subscription_item") != item.get("id") or line.get("proration") is not False
                or line.get("price", {}).get("id") != price_id or line.get("quantity") != 1
                or line.get("period") != {"start": start, "end": end} or not invoice.get("charge")):
            return denied
        charge_id = self._id(invoice["charge"], "ch_")
        charge = await self.http.request("GET", "/v1/charges/" + charge_id)
        self._object(charge, "charge", customer=customer_id, object_id=charge_id)
        if (charge.get("invoice") != invoice_id or charge.get("paid") is not True
                or charge.get("captured") is not True or charge.get("status") != "succeeded"
                or charge.get("refunded") is not False or charge.get("disputed") is not False
                or charge.get("amount_refunded") != 0 or charge.get("amount_captured") != invoice["amount_paid"]):
            return denied
        return SubscriptionSnapshot(customer_id, sub_id, "active", price_id, start, end, end, True,
                                    sub.get("cancel_at_period_end") is True)

    async def cancel_for_erasure(self, account, operations, request_id):
        customer = account.get("customer_id")
        blocked = CancellationReceipt(customer, False, False)
        # Worker never recreates an uncertain customer/session (nor needs email).
        # A lost creation acknowledgement requires operator reconciliation.
        if not customer or any(op.get("state") != "complete" or not op.get("object_id") for op in operations):
            return blocked
        customer = self._id(customer, "cus_")
        sessions = await self._list("/v1/checkout/sessions", customer, "checkout.session")
        known = {row["id"] for row in sessions}
        if any(op["kind"] == "checkout" and op["object_id"] not in known for op in operations):
            return blocked
        for session in sessions:
            if session.get("status") == "open":
                session_id = self._id(session["id"], "cs_test_")
                expired = await self.http.request("POST", "/v1/checkout/sessions/" + session_id + "/expire")
                self._object(expired, "checkout.session", customer=customer, object_id=session_id)
                if expired.get("status") != "expired":
                    return blocked
            elif session.get("status") not in ("complete", "expired"):
                return blocked
        subscriptions = await self._list("/v1/subscriptions", customer, "subscription", status="all")
        for sub in subscriptions:
            if sub.get("status") not in self.terminal:
                sub_id = self._id(sub["id"], "sub_")
                result = await self.http.request("DELETE", "/v1/subscriptions/" + sub_id,
                                                 data={"invoice_now": "false", "prorate": "false"})
                self._object(result, "subscription", customer=customer, object_id=sub_id)
                if result.get("status") != "canceled":
                    return blocked
        # Independent refetch after mutations: lost acknowledgements/retries do
        # not re-cancel terminal subscriptions. No database receipt until ALL pass.
        subscriptions = await self._list("/v1/subscriptions", customer, "subscription", status="all")
        sessions = await self._list("/v1/checkout/sessions", customer, "checkout.session")
        schedules = await self._list("/v1/subscription_schedules", customer, "subscription_schedule")
        invoices = await self._list("/v1/invoices", customer, "invoice")
        pending = await self._list("/v1/invoiceitems", customer, "invoiceitem", pending="true")
        if (any(sub.get("status") not in self.terminal for sub in subscriptions)
                or any(row.get("status") not in ("complete", "expired") for row in sessions)
                or any(row.get("status") not in ("canceled", "completed", "released") for row in schedules)
                or any(row.get("status") not in ("paid", "void") for row in invoices) or pending):
            return blocked
        return CancellationReceipt(customer, True, True)


class BillingStore:
    """Service-role-only RPC client, isolated from user-bearer MobileRepository."""
    def __init__(self, url: str, service_key: str, *, transport=None):
        if (not _https(url) or urlsplit(url).path not in ("", "/") or urlsplit(url).query
                or urlsplit(url).fragment or not isinstance(service_key, str) or len(service_key) > 8192
                or not (re.fullmatch(r"sb_secret_[A-Za-z0-9_-]+", service_key)
                        or (_jwt_role(service_key) == "service_role" and not re.search(r"\s", service_key)))):
            raise unavailable()
        self._url, self._key, self._transport = url.rstrip("/"), service_key, transport

    @classmethod
    def from_env(cls, *, transport=None):
        # Pairwise fallback only: never mix projects/keys. Matches the existing
        # privacy worker when separate billing credentials were not configured.
        if "MOBILE_BILLING_SUPABASE_URL" in os.environ or "MOBILE_BILLING_SERVICE_ROLE_KEY" in os.environ:
            url, key = os.environ.get("MOBILE_BILLING_SUPABASE_URL", ""), os.environ.get("MOBILE_BILLING_SERVICE_ROLE_KEY", "")
        else:
            url, key = os.environ.get("MOBILE_PRIVACY_SUPABASE_URL", ""), os.environ.get("MOBILE_PRIVACY_SUPABASE_SECRET_KEY", "")
        return cls(url, key, transport=transport)

    async def rpc(self, name: str, **params) -> dict:
        if not re.fullmatch(r"mobile_billing_[a-z_]+", name):
            raise ValueError("Invalid billing RPC")
        headers = {"apikey": self._key}
        if not self._key.startswith("sb_secret_"):
            headers["Authorization"] = "Bearer " + self._key
        async with httpx.AsyncClient(base_url=self._url, transport=self._transport, trust_env=False,
                follow_redirects=False, timeout=httpx.Timeout(20, connect=5),
                headers=headers) as client:
            result = await _bounded_request(client, "POST", "/rest/v1/rpc/" + name, json=params)
        if result.get("status") in ("busy", "conflict", "stale", "blocked"):
            raise unavailable()
        return result


class BillingService:
    def __init__(self, settings: BillingSettings, store: BillingStore,
                 provider: Optional[BillingProvider] = None, *, clock=time.time):
        if provider is not None and (provider.name != settings.provider or provider.mode != settings.mode):
            raise ValueError("Billing adapter/configuration mismatch")
        if isinstance(provider, StripeTestProvider):
            for plan in settings.plans.values():
                provider._id(plan.price_id, "price_")
        self.settings, self.store, self.provider, self.clock = settings, store, provider, clock

    @classmethod
    def from_env(cls, *, transport=None, store_transport=None):
        settings = BillingSettings.from_env()
        provider = StripeTestProvider.from_env(transport=transport) if settings.provider == "stripe" else None
        return cls(settings, BillingStore.from_env(transport=store_transport), provider)

    def _provider(self):
        if self.provider is None:
            raise unavailable()
        return self.provider

    @staticmethod
    def _identity(repo):
        user_id = _uuid(repo.user_id)
        email = repo.verified_email
        if not isinstance(email, str) or not re.fullmatch(r"[^\s<>@]{1,120}@[^\s<>@]{1,120}", email):
            raise HTTPException(403, "A remotely verified email is required for billing.")
        return user_id, email

    async def _release(self, account):
        try:
            await self.store.rpc("mobile_billing_release", p_account_id=account["id"], p_lease_token=account["lease_token"])
        except Exception:
            # The durable lease expires; operations remain pending for recovery.
            pass

    async def checkout(self, repo, plan_key: str) -> dict:
        if not self.settings.checkout_enabled:
            raise unavailable()
        provider = self._provider()
        if isinstance(provider, StripeTestProvider) and not provider._portal_configuration:
            raise unavailable()
        if not isinstance(plan_key, str) or plan_key not in self.settings.plans:
            raise HTTPException(422, "Choose a configured billing plan.")
        user_id, email = self._identity(repo)
        account = await self.store.rpc("mobile_billing_begin", p_user_id=user_id,
                                       p_provider=provider.name, p_mode=provider.mode)
        try:
            if not account.get("customer_id"):
                fingerprint = hashlib.sha256((user_id + "\n" + email).encode()).hexdigest()
                operation = await self.store.rpc("mobile_billing_operation", p_account_id=account["id"],
                    p_lease_token=account["lease_token"], p_kind="customer", p_fingerprint=fingerprint,
                    p_plan_key=None, p_price_id=None)
                customer = await provider.create_customer(user_id, email, operation)
                await self.store.rpc("mobile_billing_complete_operation", p_account_id=account["id"],
                    p_lease_token=account["lease_token"], p_operation_id=operation["id"],
                    p_object_id=customer, p_url=None, p_expires_at=None)
                account["customer_id"] = customer
            current = await provider.fetch_subscription(account["customer_id"])
            if current.customer_id != account["customer_id"]:
                raise unavailable()
            if current.status not in ("none", "canceled", "incomplete_expired"):
                raise HTTPException(409, "A subscription already exists or needs reconciliation. Use the billing portal.")
            plan = self.settings.plans[plan_key]
            parameters = (plan.price_id, self.settings.success_url, self.settings.cancel_url, user_id)
            fingerprint = hashlib.sha256(json.dumps(parameters).encode()).hexdigest()
            operation = await self.store.rpc("mobile_billing_operation", p_account_id=account["id"],
                p_lease_token=account["lease_token"], p_kind="checkout", p_fingerprint=fingerprint,
                p_plan_key=plan_key, p_price_id=plan.price_id)
            result = await provider.create_checkout(account["customer_id"], user_id, plan, self.settings, operation)
            if (not _https(result.get("url"), provider.checkout_hosts)
                    or type(result.get("expires_at")) is not int or result["expires_at"] <= self.clock()):
                raise unavailable()
            await self.store.rpc("mobile_billing_complete_operation", p_account_id=account["id"],
                p_lease_token=account["lease_token"], p_operation_id=operation["id"],
                p_object_id=result["id"], p_url=result["url"], p_expires_at=result["expires_at"])
            return {"url": result["url"]}  # NEVER changes membership/quota.
        except HTTPException:
            raise
        except Exception:
            raise unavailable() from None
        finally:
            await self._release(account)

    async def portal(self, repo) -> dict:
        provider = self._provider()
        user_id, _ = self._identity(repo)
        if not _https(self.settings.portal_return_url):
            raise unavailable()
        account = await self.store.rpc("mobile_billing_begin", p_user_id=user_id,
                                       p_provider=provider.name, p_mode=provider.mode)
        try:
            if not account.get("customer_id"):
                raise HTTPException(409, "No billing customer exists for this account.")
            result = await provider.create_portal(account["customer_id"], self.settings.portal_return_url)
            if not _https(result.get("url"), provider.portal_hosts):
                raise unavailable()
            return {"url": result["url"]}
        except HTTPException:
            raise
        except Exception:
            raise unavailable() from None
        finally:
            await self._release(account)

    def _snapshot(self, snapshot: SubscriptionSnapshot, customer: str) -> dict:
        if snapshot.customer_id != customer or type(snapshot.paid) is not bool or type(snapshot.cancel_at_period_end) is not bool:
            raise unavailable()
        plan_entry = next(((key, plan) for key, plan in self.settings.plans.items() if plan.price_id == snapshot.price_id), None)
        dates = (snapshot.period_start, snapshot.period_end, snapshot.paid_through)
        valid_dates = all(type(value) is int and 0 < value < 4_102_444_800 for value in dates)
        eligible = bool(plan_entry and snapshot.status == "active" and snapshot.paid and valid_dates
                        and snapshot.period_start <= self.clock() < min(snapshot.period_end, snapshot.paid_through)
                        and snapshot.period_start < snapshot.period_end and snapshot.subscription_id)
        return {"subscription_id": snapshot.subscription_id, "status": snapshot.status,
                "plan_key": plan_entry[0] if plan_entry else None, "eligible": eligible,
                "period_start": snapshot.period_start if valid_dates else None,
                "period_end": snapshot.period_end if valid_dates else None,
                "paid_through": min(snapshot.period_end, snapshot.paid_through) if valid_dates else None,
                "cancel_at_period_end": snapshot.cancel_at_period_end,
                "period_limit": plan_entry[1].period_limit if eligible else 0,
                "daily_limit": plan_entry[1].daily_limit if eligible else 0}

    async def webhook(self, raw_body: bytes, signature: str) -> dict:
        provider = self._provider()
        if not isinstance(raw_body, bytes) or not 0 < len(raw_body) <= MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "Billing webhook body is too large or empty.")
        event = provider.verify_webhook(raw_body, signature, int(self.clock()))
        if event is None:
            return {"status": "ignored"}
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", event.id)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", event.customer_id)
                or event.body_sha256 != hashlib.sha256(raw_body).hexdigest()):
            raise HTTPException(400, "Invalid billing event.")
        account = await self.store.rpc("mobile_billing_event_claim", p_provider=provider.name, p_mode=provider.mode,
            p_event_id=event.id, p_customer_id=event.customer_id, p_body_sha256=event.body_sha256)
        if account.get("status") in ("duplicate", "ignored"):
            return {"status": account["status"]}
        try:
            # Claim BEFORE fetch prevents concurrent workers applying snapshots
            # in reverse order. Expired leases are fenced at SQL apply time.
            snapshot = await provider.fetch_subscription(account["customer_id"])
            result = await self.store.rpc("mobile_billing_apply_snapshot", p_account_id=account["id"],
                p_lease_token=account["lease_token"], p_event_id=event.id,
                p_snapshot=self._snapshot(snapshot, account["customer_id"]))
            return {"status": result["status"]}
        except HTTPException:
            raise
        except Exception:
            raise unavailable() from None
        finally:
            await self._release(account)

    async def cancel_for_erasure(self, user_id: str, request_id: str) -> dict:
        """WORKER ONLY: caller already holds a verified-user durable erasure claim.

        No email or user-bearer repository required. Return ready only after SQL
        records a matching cancellation receipt. Never deletes Auth/vendor data.
        """
        user_id, request_id = _uuid(user_id), _uuid(request_id)
        result = {"status": "blocked", "request_id": request_id, "code": "billing_cancellation_unconfirmed"}
        account = None
        try:
            account = await self.store.rpc("mobile_billing_cancel_claim", p_user_id=user_id, p_request_id=request_id)
            if account.get("status") == "ready":
                return {**result, "status": "ready", "code": "billing_cancellation_confirmed"}
            provider = self._provider()
            if account.get("provider") != provider.name or account.get("mode") != provider.mode:
                return result
            operations = account.get("operations")
            if not isinstance(operations, list):
                return result
            receipt = await provider.cancel_for_erasure(account, operations, request_id)
            if (not isinstance(receipt, CancellationReceipt) or receipt.customer_id != account.get("customer_id")
                    or receipt.cancellation_confirmed is not True or receipt.no_future_collection is not True
                    or receipt.open_subscription_ids or receipt.open_checkout_ids or receipt.unresolved_operation_ids):
                return result
            finished = await self.store.rpc("mobile_billing_cancel_finish", p_account_id=account["id"],
                p_request_id=request_id, p_lease_token=account["lease_token"], p_receipt={
                    "customer_id": receipt.customer_id, "cancellation_confirmed": True, "no_future_collection": True,
                    "open_subscription_ids": [], "open_checkout_ids": [], "unresolved_operation_ids": [],
                })
            if finished.get("status") == "ready":
                return {**result, "status": "ready", "code": "billing_cancellation_confirmed"}
        except Exception:
            # No secret/provider diagnostic can enter a worker queue or receipt.
            return result
        finally:
            if account and account.get("lease_token"):
                await self._release(account)
        return result


async def cancel_for_erasure(user_id: str, request_id: str, *, service=None,
                             transport=None, store_transport=None) -> dict:
    """Trusted account-worker callback, never bind user_id to a public input."""
    user_id, request_id = _uuid(user_id), _uuid(request_id)
    try:
        service = service or BillingService.from_env(transport=transport, store_transport=store_transport)
        return await service.cancel_for_erasure(user_id, request_id)
    except Exception:
        return {"status": "blocked", "request_id": request_id, "code": "billing_cancellation_unconfirmed"}


# Compatibility for the worker, WITHOUT MinimalRepo or queued email/credentials.
prepare_account_erasure = cancel_for_erasure


def billing_capabilities(service=None) -> dict:
    """Safe config-only status: no network, migration check, admin requirement or secrets.

    Missing/invalid service credentials return disabled, not an error. This is
    configuration readiness only, NEVER proof of entitlement/deployment health.
    Explicit service injection supports tests; there is no runtime fake switch.
    """
    disabled = {"provider": None, "mode": "test", "checkout_enabled": False,
                "portal_enabled": False, "plan_keys": [], "configuration_ready": False}
    try:
        service = service if service is not None else BillingService.from_env()
        settings, provider = service.settings, service.provider
        if provider is None:
            return disabled
        portal_ready = bool(_https(settings.portal_return_url) and
                            (not isinstance(provider, StripeTestProvider) or provider._portal_configuration))
        checkout_ready = bool(settings.checkout_enabled and portal_ready)
        return {**disabled, "provider": provider.name, "mode": provider.mode,
                "checkout_enabled": checkout_ready, "portal_enabled": portal_ready,
                "plan_keys": sorted(settings.plans) if checkout_ready else [], "configuration_ready": True}
    except Exception:
        return disabled
