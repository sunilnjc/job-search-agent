import assert from "node:assert/strict";
import { test } from "node:test";
import { billingActions, billingCheckoutPayload, checkedBillingStatus, checkedBillingUrl } from "../src/beta/billing.ts";
import { createMobileTransport } from "../src/beta/mobileTransport.ts";

const ready = { provider: "stripe", mode: "test", checkout_enabled: true, portal_enabled: true, configuration_ready: true, plan_keys: ["operator_test"], subscription: null, subscription_status: "none" };
test("disabled/unloaded billing is not treated as a free subscription or enabled checkout", () => {
  const disabled = { ...ready, provider: null, checkout_enabled: false, portal_enabled: false, configuration_ready: false, plan_keys: [], subscription_status: "not_loaded" };
  assert.deepEqual(checkedBillingStatus(disabled), disabled);
  assert.deepEqual(billingActions(disabled), { checkout: false, portal: false });
  assert.deepEqual(billingActions(null), { checkout: false, portal: false });
  assert.equal(billingActions({ ...ready, provider: "unrecognised" }).checkout, false);
});
test("billing accepts test mode only and requires coherent service subscription state", () => {
  assert.deepEqual(checkedBillingStatus(ready), ready);
  for (const value of [{ ...ready, mode: "live" }, { ...ready, checkout_enabled: "true" }, { ...ready, subscription_status: "active" }, { ...ready, subscription: {} }, { ...ready, plan_keys: [null] }]) assert.throws(() => checkedBillingStatus(value));
});
test("existing subscriptions use portal instead of starting another checkout", () => {
  const value = checkedBillingStatus({ ...ready, subscription_status: "active", subscription: { status: "active", plan_key: "operator_test", period_start: null, paid_through: null, cancel_at_period_end: true, cancellation_status: "scheduled" } });
  assert.deepEqual(billingActions(value), { checkout: false, portal: true });
});
test("checkout only sends an allowlisted server plan key, never price, identity or return URL", () => {
  assert.deepEqual(billingCheckoutPayload(ready, "operator_test"), { plan_key: "operator_test" });
  assert.throws(() => billingCheckoutPayload(ready, "invented"));
  assert.throws(() => billingCheckoutPayload({ ...ready, checkout_enabled: false }, "operator_test"));
});
test("only exact HTTPS Stripe checkout and portal hosts are accepted, URL preserved unchanged", () => {
  const checkout = "https://checkout.stripe.com/c/pay/cs_test_fixture#original";
  assert.equal(checkedBillingUrl({ url: checkout }, "checkout"), checkout);
  assert.equal(checkedBillingUrl({ url: "https://billing.stripe.com/p/session/test_fixture" }, "portal"), "https://billing.stripe.com/p/session/test_fixture");
  for (const url of ["http://checkout.stripe.com/x", "https://checkout.stripe.com.evil.example/x", "https://evil.example/checkout.stripe.com", "https://checkout.stripe.com@evil.example/x", "https://someone@checkout.stripe.com/x", "https://checkout.stripe.com:444/x", "//checkout.stripe.com/x", "javascript:alert(1)", "https://billing.stripe.com/x", " https://checkout.stripe.com/x", "https://checkout.stripe.com/\nx"]) assert.throws(() => checkedBillingUrl({ url }, "checkout"));
});
test("billing uses authenticated mobile API, explicit checkout/portal requests, no client access grant", async () => {
  const calls = [];
  const request = createMobileTransport(async () => ({ user: { id: "fixture" }, access_token: "fixture-token" }), async (url, options) => { calls.push([url, options]); return new Response(JSON.stringify(ready)); });
  await request("fixture", "/billing");
  await request("fixture", "/billing/checkout", { method: "POST", body: billingCheckoutPayload(ready, "operator_test") });
  await request("fixture", "/billing/portal", { method: "POST", body: {} });
  assert.deepEqual(calls.map(([url]) => url), ["/api/mobile/billing", "/api/mobile/billing/checkout", "/api/mobile/billing/portal"]);
  assert.equal(calls[1][1].body, '{"plan_key":"operator_test"}'); assert.equal(calls[2][1].body, "{}");
  for (const [, options] of calls) { assert.equal(options.headers.Authorization, "Bearer fixture-token"); assert.equal(options.credentials, "omit"); }
});
