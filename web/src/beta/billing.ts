export type BillingSubscription = {
  status: string; plan_key: string | null; period_start: string | null; paid_through: string | null;
  cancel_at_period_end: boolean | null; cancellation_status: string | null;
};
export type BillingStatus = {
  provider: string | null; mode: "test"; checkout_enabled: boolean; portal_enabled: boolean;
  plan_keys: string[]; configuration_ready: boolean; subscription: BillingSubscription | null;
  subscription_status: string;
};

const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const short = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 200;
const nullable = (value: unknown) => value === null || short(value);
export function checkedBillingStatus(value: unknown): BillingStatus {
  if (!record(value) || value.mode !== "test" || !nullable(value.provider)
    || ["checkout_enabled", "portal_enabled", "configuration_ready"].some(key => typeof value[key] !== "boolean")
    || !Array.isArray(value.plan_keys) || value.plan_keys.length > 100 || !value.plan_keys.every(short)
    || !short(value.subscription_status)) throw new Error("Billing test-mode status could not be verified. Refresh billing or contact the service operator; no checkout was opened.");
  const sub = value.subscription;
  if (sub !== null && (!record(sub) || !short(sub.status) || sub.status !== value.subscription_status
    || !["plan_key", "period_start", "paid_through", "cancellation_status"].every(key => nullable(sub[key]))
    || !(sub.cancel_at_period_end === null || typeof sub.cancel_at_period_end === "boolean"))) throw new Error("Subscription status could not be verified. Refresh billing before continuing.");
  if (sub === null && !["none", "not_loaded"].includes(value.subscription_status)) throw new Error("Subscription status is incomplete. Refresh billing before continuing.");
  return value as BillingStatus;
}
export function billingActions(status: BillingStatus | null) {
  const configured = !!status && status.provider === "stripe" && status.mode === "test" && status.configuration_ready;
  return {
    checkout: configured && status.checkout_enabled && status.subscription_status === "none" && status.plan_keys.length > 0,
    portal: configured && status.portal_enabled && status.subscription_status !== "not_loaded",
  };
}
export function billingCheckoutPayload(status: BillingStatus, planKey: string) {
  if (!billingActions(status).checkout || !status.plan_keys.includes(planKey)) throw new Error("Choose an available server-configured test plan after refreshing billing.");
  return { plan_key: planKey };
}
/** Only an API response may supply a billing destination; never a return query parameter. */
export function checkedBillingUrl(value: unknown, kind: "checkout" | "portal"): string {
  if (record(value) && typeof value.url === "string" && value.url.length <= 8192 && !/\s/.test(value.url)) {
    try {
      const url = new URL(value.url);
      const host = kind === "checkout" ? "checkout.stripe.com" : "billing.stripe.com";
      if (url.protocol === "https:" && url.hostname === host && !url.username && !url.password && !url.port) return value.url;
    } catch { /* Refuse malformed or non-allowlisted links. */ }
  }
  throw new Error("The service returned an untrusted billing link. Nothing was opened. Contact the service operator before requesting another link.");
}
