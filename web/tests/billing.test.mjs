import assert from "node:assert/strict";
import { test } from "node:test";
import { billingActions, billingCheckoutPayload, checkedBillingStatus, checkedBillingUrl, checkedCustomerBillingStatus, checkedBillingPlans, billingPriceLabel, billingRouteRequested } from "../src/beta/billing.ts";
import { createMobileTransport } from "../src/beta/mobileTransport.ts";

const ready = { provider: "stripe", mode: "test", checkout_enabled: true, portal_enabled: true, configuration_ready: true, plan_keys: ["operator_test"], subscription: null, subscription_status: "none" };
const account = { ...ready, account_exists: false, reconciliation_pending: false, access: {allowed:false, grant_source:null, expires_at:null, period_end:null, period_remaining:0, daily_remaining:0} };
const plan = {plan_key:"operator_test", display_name:"Sandbox Career", amount_minor:321, currency:"usd", interval:"month", interval_count:1, period_limit:100, daily_limit:10};
test("billing return intents select a view only, reject ambiguous or forged paid hints", () => {
  for (const value of ["plans","return","cancelled"]) assert.equal(billingRouteRequested("?billing="+value),true);
  for (const search of ["?paid=true","?checkout=success","?billing=active","?billing=return&billing=plans"]) assert.equal(billingRouteRequested(search),false);
});
test("customer access requires actual owner-bound quota state, not an active subscription string", () => {
  assert.deepEqual(checkedCustomerBillingStatus(account), account);
  for (const value of [ready, {...account,access:{...account.access,allowed:"true"}}, {...account,access:{...account.access,period_remaining:-1}}, {...account,access:{...account.access,grant_source:"checkout"}}]) assert.throws(() => checkedCustomerBillingStatus(value));
});
test("public pricing accepts only bounded fixed recurring test prices and rejects ambiguous money", () => {
  assert.deepEqual(checkedBillingPlans({mode:"test",plans:[plan]}), [plan]);
  assert.match(billingPriceLabel(plan), /3\.21.*month/);
  for (const value of [{mode:"live",plans:[plan]}, {mode:"test",plans:[plan,plan]}, ...[{amount_minor:0}, {amount_minor:3.21}, {currency:"jpy"}, {interval_count:12}, {daily_limit:Infinity}].map(update => ({mode:"test",plans:[{...plan,...update}]}))]) assert.throws(() => checkedBillingPlans(value));
});
test("explicit reconciliation sends no client price, access, identity or checkout receipt", async () => {
  const calls=[];
  const request=createMobileTransport(async()=>({user:{id:"fixture"},access_token:"fixture-token"}), async(url,options)=>{calls.push([url,options]);return new Response('{}');});
  await request("fixture","/billing/reconcile",{method:"POST",body:{}});
  assert.equal(calls[0][0],"/api/mobile/billing/reconcile"); assert.equal(calls[0][1].body,"{}");
  assert.equal(calls[0][1].credentials,"omit");
});
test("live UI requires explicit server activation and matching-mode prices", () => {
  const live={...account,mode:"live",live_activation_approved:true};
  assert.equal(checkedCustomerBillingStatus(live).mode,"live");
  assert.equal(billingActions(live).checkout,true);
  assert.equal(billingActions({...live,configuration_ready:false}).checkout,false);
  assert.equal(billingActions({...live,live_activation_approved:false}).checkout,false);
  assert.deepEqual(checkedBillingPlans({mode:"live",plans:[plan]},"live"),[plan]);
  assert.throws(()=>checkedBillingPlans({mode:"test",plans:[plan]},"live"));
  assert.throws(()=>checkedCustomerBillingStatus({...live,live_activation_approved:false}));
});
test("disabled/unloaded billing is not treated as a free subscription or enabled checkout", () => {
  const disabled = { ...ready, provider: null, checkout_enabled: false, portal_enabled: false, configuration_ready: false, plan_keys: [], subscription_status: "not_loaded" };
  assert.deepEqual(checkedBillingStatus(disabled), disabled);
  assert.deepEqual(billingActions(disabled), { checkout: false, portal: false });
  assert.deepEqual(billingActions(null), { checkout: false, portal: false });
  assert.equal(billingActions({ ...ready, provider: "unrecognised" }).checkout, false);
});
test("billing rejects unapproved live mode and requires coherent service subscription state", () => {
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
