export type BillingSubscription = {
  status: string; plan_key: string | null; period_start: string | null; paid_through: string | null;
  cancel_at_period_end: boolean | null; cancellation_status: string | null;
};
export type BillingStatus = {
  provider: string | null; mode: "test" | "live"; live_activation_approved?: boolean; checkout_enabled: boolean; portal_enabled: boolean;
  plan_keys: string[]; configuration_ready: boolean; subscription: BillingSubscription | null;
  subscription_status: string;
};

const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const short = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 200;
const nullable = (value: unknown) => value === null || short(value);
/** View-selection hint only. Never payment proof, a POST trigger or entitlement. */
export function billingRouteRequested(search: string): boolean {
  const values = new URLSearchParams(search).getAll("billing");
  return values.length === 1 && ["plans", "return", "cancelled"].includes(values[0]);
}
/** Pure checkout-return location. Does not touch history; the caller may replaceState. */
export function billingReturnLocation(search: string, pathname: string, hash = ""): { open: boolean; href: string } {
  const open = billingRouteRequested(search);
  const params = new URLSearchParams(search);
  if (open) params.delete("billing");
  const next = params.toString();
  return { open, href: pathname + (next ? `?${next}` : "") + hash };
}
export type CustomerBillingStatus = BillingStatus & {
  account_exists: boolean; reconciliation_pending: boolean; mode_mismatch?: boolean;
  access: { allowed: boolean; grant_source: "manual" | "billing" | null;
    expires_at: string | null; period_end: string | null; period_remaining: number; daily_remaining: number };
};
export type BillingPlan = { plan_key: string; display_name: string; amount_minor: number;
  currency: string; interval: "month" | "year"; interval_count: 1; period_limit: number; daily_limit: number };
const units = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= 9_000_000_000_000_000;
export function checkedCustomerBillingStatus(value: unknown): CustomerBillingStatus {
  checkedBillingStatus(value);
  if (!record(value) || typeof value.account_exists !== "boolean" || typeof value.reconciliation_pending !== "boolean"
    || !record(value.access) || typeof value.access.allowed !== "boolean"
    || ![null, "manual", "billing"].includes(value.access.grant_source as null | string)
    || !nullable(value.access.expires_at) || !nullable(value.access.period_end)
    || !units(value.access.period_remaining) || !units(value.access.daily_remaining))
    throw new Error("Workspace access is unknown. Refresh billing before continuing; payment has not been confirmed.");
  return value as CustomerBillingStatus;
}
export function checkedBillingPlans(value: unknown, mode: "test" | "live" = "test"): BillingPlan[] {
  if (!record(value) || value.mode !== mode || !Array.isArray(value.plans) || value.plans.length > 50
    || !value.plans.every(p => record(p) && typeof p.plan_key === "string" && /^[a-z][a-z0-9_-]{0,39}$/.test(p.plan_key)
      && typeof p.display_name === "string" && p.display_name.length > 0 && p.display_name.length <= 80
      && Number.isSafeInteger(p.amount_minor) && Number(p.amount_minor) > 0 && Number(p.amount_minor) <= 99_999_999
      && ["usd", "eur", "gbp", "aed", "inr", "cad", "aud", "sgd"].includes(String(p.currency))
      && ["month", "year"].includes(String(p.interval)) && p.interval_count === 1
      && units(p.period_limit) && Number(p.period_limit) > 0 && units(p.daily_limit) && Number(p.daily_limit) > 0)
    || new Set(value.plans.map(p => p.plan_key)).size !== value.plans.length)
    throw new Error("Plan prices could not be verified. Refresh billing before choosing a plan.");
  return value.plans as BillingPlan[];
}
export function billingPriceLabel(plan: BillingPlan): string {
  return `${new Intl.NumberFormat(undefined, { style: "currency", currency: plan.currency.toUpperCase() }).format(plan.amount_minor / 100)} / ${plan.interval}`;
}
export function checkedBillingStatus(value: unknown): BillingStatus {
  if (!record(value) || !["test", "live"].includes(String(value.mode))
    || (value.mode === "live" && value.live_activation_approved !== true) || !nullable(value.provider)
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
  const configured = !!status && status.provider === "stripe"
    && (status.mode === "test" || (status.mode === "live" && status.live_activation_approved === true)) && status.configuration_ready;
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
