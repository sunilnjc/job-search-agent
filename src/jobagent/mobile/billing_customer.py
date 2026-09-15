"""Customer billing router: invited verified identity, never paid access.

Include via customer_billing_router(billing_service, invited_repository).
Existing checkout/portal/webhook endpoints remain owned by app.py. No billing
keys in browser or user repo; service-only reconciliation uses BillingStore.
"""
from __future__ import annotations

import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .billing import BillingService, billing_capabilities


class ReconcileRequest(BaseModel):
    class Config:
        extra = "forbid"


def customer_billing_router(billing_service, invited_repository) -> APIRouter:
    router = APIRouter(prefix="/api/mobile/billing", tags=["billing"])

    @router.get("/plans")
    async def plans(repo=Depends(invited_repository)):
        BillingService._identity(repo)
        return await billing_service().plans()

    @router.get("/account")
    async def account(repo=Depends(invited_repository)):
        user_id, _ = BillingService._identity(repo)
        try:
            raw = await repo.request(repo.client, "POST", "/rest/v1/rpc/mobile_billing_customer_status",
                                     json={}, max_bytes=16*1024)
            state = json.loads(raw)
            if (not isinstance(state, dict) or state.get("user_id") != user_id
                    or not isinstance(state.get("access"), dict)
                    or type(state["access"].get("allowed")) is not bool
                    or type(state.get("account_exists")) is not bool
                    or type(state.get("reconciliation_pending")) is not bool):
                raise ValueError
        except (HTTPException, ValueError, TypeError):
            raise HTTPException(503, "Your billing access could not be loaded. Refresh billing; if this persists, "
                                "ask support to verify customer billing migration 0013. No access was changed.") from None
        # Config is not payment proof. A missing provider must not erase the
        # valid manual/access state returned by the independent owner RPC.
        try:
            capabilities = billing_capabilities(billing_service())
        except HTTPException:
            capabilities = {"provider": None, "mode": "test", "checkout_enabled": False,
                            "portal_enabled": False, "plan_keys": [], "configuration_ready": False}
        sub = state.get("subscription")
        mismatch = state.get("billing_mode") is not None and state["billing_mode"] != capabilities["mode"]
        if mismatch or (capabilities["mode"] == "live" and state.get("live_activation_enabled") is not True):
            capabilities.update(checkout_enabled=False, portal_enabled=False, configuration_ready=False, plan_keys=[])
        return {**capabilities, **{key: state[key] for key in
                ("account_exists", "reconciliation_pending", "access")},
                "mode_mismatch": mismatch,
                "subscription": sub, "subscription_status": sub["status"] if sub else "none"}

    @router.post("/reconcile")
    async def reconcile(body: ReconcileRequest, repo=Depends(invited_repository)):
        return await billing_service().reconcile(repo)

    return router
