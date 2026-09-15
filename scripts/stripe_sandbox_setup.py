"""One-off Stripe SANDBOX setup: customer portal configuration + webhook endpoint.

Reads MOBILE_BILLING_STRIPE_SECRET_KEY from .env.mobile (test keys only), creates
objects matching BillingService's checks, and writes their IDs/secrets back into
.env.mobile. Secrets are never printed. Re-running skips values already set.
"""
import argparse
import re
from pathlib import Path
from urllib.parse import urlencode

import httpx

API_VERSION = "2024-06-20"  # must equal StripeTestProvider.api_version
WEBHOOK_URL = "https://www.thejobpursuit.com/api/mobile/billing/webhook"
RETURN_URL = "https://www.thejobpursuit.com/beta?billing=return"
EVENTS = (
    [f"customer.subscription.{a}" for a in ("created", "updated", "deleted", "paused", "resumed",
                                             "pending_update_applied", "pending_update_expired")]
    + [f"invoice.{a}" for a in ("paid", "payment_failed", "payment_action_required", "voided",
                                "marked_uncollectible", "updated")]
    + ["checkout.session.completed", "checkout.session.expired", "charge.refunded", "charge.updated"]
)


def read_env(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def write_env(path: Path, key: str, value: str) -> None:
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env.mobile")
    env_path = Path(parser.parse_args().env_file)
    env = read_env(env_path)
    key = env.get("MOBILE_BILLING_STRIPE_SECRET_KEY", "")
    if not key.startswith("sk_test_"):
        print("Refusing: MOBILE_BILLING_STRIPE_SECRET_KEY is missing or not a sk_test_ key.")
        return 1
    client = httpx.Client(base_url="https://api.stripe.com", timeout=30,
                          headers={"Authorization": "Bearer " + key, "Stripe-Version": API_VERSION})

    if env.get("MOBILE_BILLING_STRIPE_PORTAL_CONFIGURATION", "").startswith("bpc_"):
        print("Portal configuration already set; skipped.")
    else:
        response = client.post("/v1/billing_portal/configurations", data={
            "business_profile[headline]": "Manage your Job Pursuit subscription",
            "default_return_url": RETURN_URL,
            "features[subscription_cancel][enabled]": "true",
            "features[subscription_cancel][mode]": "at_period_end",
            "features[subscription_update][enabled]": "false",
            "features[payment_method_update][enabled]": "true",
            "features[invoice_history][enabled]": "true",
        })
        response.raise_for_status()
        write_env(env_path, "MOBILE_BILLING_STRIPE_PORTAL_CONFIGURATION", response.json()["id"])
        print("Created portal configuration:", response.json()["id"])

    if env.get("MOBILE_BILLING_STRIPE_WEBHOOK_SECRET", "").startswith("whsec_"):
        print("Webhook secret already set; skipped.")
    else:
        existing = client.get("/v1/webhook_endpoints", params={"limit": 100})
        existing.raise_for_status()
        if any(item["url"] == WEBHOOK_URL for item in existing.json()["data"]):
            print("Refusing: an endpoint for this URL exists but its secret is not in the env file. "
                  "Delete or roll it in the dashboard, then re-run.")
            return 1
        data = [("url", WEBHOOK_URL), ("api_version", API_VERSION),
                ("description", "Job Pursuit sandbox billing")]
        data += [("enabled_events[]", event) for event in EVENTS]
        # Repeated enabled_events[] keys need explicit form encoding.
        response = client.post("/v1/webhook_endpoints", content=urlencode(data),
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
        response.raise_for_status()
        write_env(env_path, "MOBILE_BILLING_STRIPE_WEBHOOK_SECRET", response.json()["secret"])
        print("Created webhook endpoint:", response.json()["id"], "(signing secret written, not shown)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
