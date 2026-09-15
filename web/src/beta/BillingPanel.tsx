import { useCallback, useEffect, useRef, useState } from "react";
import { mobileRequest } from "./mobile";
import { billingActions, billingCheckoutPayload, checkedCustomerBillingStatus, checkedBillingUrl, checkedBillingPlans, billingPriceLabel } from "./billing";
import type { CustomerBillingStatus, BillingPlan } from "./billing";
import "./studio-workflow.css";
import "./billing.css";

export function BillingPanel({ userId, onBack, onPrivacy }: { userId: string; onBack: () => void; onPrivacy: () => void }) {
  const [status, setStatus] = useState<CustomerBillingStatus | null>(null);
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [plan, setPlan] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [link, setLink] = useState<{ kind: "checkout" | "portal"; url: string } | null>(null);
  const request = useRef<AbortController | null>(null);
  const load = useCallback(async (signal: AbortSignal) => {
    setStatus(null); setPlans([]); setLink(null);
    const next = checkedCustomerBillingStatus(await mobileRequest(userId, "/billing/account", { signal }));
    if (signal.aborted) return;
    setStatus(next); setUncertain(false);
    // A pricing failure must not hide existing access/cancellation information.
    if (next.configuration_ready && next.checkout_enabled) {
      const catalog = checkedBillingPlans(await mobileRequest(userId, "/billing/plans", { signal }), next.mode);
      if (!signal.aborted) {
        setPlans(catalog); setPlan(current => catalog.some(p => p.plan_key === current) ? current : catalog[0]?.plan_key ?? "");
      }
    }
  }, [userId]);
  const act = async (action: (signal: AbortSignal) => Promise<void>, write = false) => {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller; setBusy(true); setError(null); setNotice(null);
    try { await action(controller.signal); }
    catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof Error ? failure.message : "Billing could not be reached. Refresh billing to check status.");
        // Even a rejected/invalid link may follow provider-side creation. Never auto-repeat it.
        if (write) setUncertain(true);
      }
    } finally { if (request.current === controller) { request.current = null; setBusy(false); } }
  };
  useEffect(() => {
    const controller = new AbortController(); request.current = controller; setBusy(true);
    void load(controller.signal).catch(failure => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Billing status could not load."); })
      .finally(() => { if (request.current === controller) { request.current = null; setBusy(false); } });
    return () => { request.current?.abort(); request.current = null; };
  }, [load]);
  const actions = billingActions(status);
  const live = status?.mode === "live" && status.live_activation_approved === true;
  const selected = plans.find(p => p.plan_key === plan && status?.plan_keys.includes(p.plan_key));
  const openLink = (kind: "checkout" | "portal") => act(async signal => {
    if (!status || uncertain || (kind === "checkout" ? !actions.checkout || !selected : !actions.portal || !status.account_exists)) return;
    setLink(null);
    const body = kind === "checkout" ? billingCheckoutPayload(status, plan) : {};
    const response = await mobileRequest(userId, `/billing/${kind}`, { method: "POST", body, signal });
    const url = checkedBillingUrl(response, kind);
    if (!signal.aborted) setLink({ kind, url });
  }, true);
  const reconcile = () => act(async signal => {
    // Never auto-POST from mount, relogin or a provider return query parameter.
    await mobileRequest(userId, "/billing/reconcile", { method: "POST", body: {}, signal });
    await load(signal);
    if (!signal.aborted) setNotice("Verification finished. Current access and subscription are shown below.");
  });
  const subscription = status?.subscription;
  return <section className="beta-content workflow billing-panel" aria-labelledby="billing-title">
    <button className="beta-text-button" onClick={onBack}>← Back to workspace</button>
    <header className="pursuit-page-heading"><p className="beta-eyebrow">Plans & access{live ? "" : " · sandbox"}</p><h1 id="billing-title">Choose your job-search plan</h1><p>{live ? "Choose a plan with clear AI usage limits. Checkout opens securely with Stripe; review the recurring amount before confirming payment." : "Test the upgrade journey safely. These are configured sandbox prices, not a live offer. Use test payment details only; real payments are disabled."}</p></header>
    <p>Checkout or a return URL is not proof of payment or workspace access. Access changes only after verified server reconciliation. Refresh billing to read status; this page never grants access.</p>
    <div className="workflow-actions"><button className="beta-secondary" disabled={busy} onClick={() => void act(load)}>Refresh billing</button><button className="beta-secondary" disabled={busy || !status?.configuration_ready || (!status.account_exists && !link)} onClick={() => void reconcile()}>Verify payment & access</button><button className="beta-secondary" onClick={onPrivacy}>Account privacy</button></div>
    {busy && <p role="status">Contacting the authenticated billing service…</p>}
    {error && <p className="beta-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {uncertain && <p className="beta-notice">No automatic retry was made. Refresh billing and check with the service operator if a checkout may already exist before requesting another link.</p>}
    {status && !status.configuration_ready && <p className="beta-notice">{status.mode_mismatch ? "This account has billing records from a different payment mode. Contact support; there is no automatic sandbox-to-live conversion." : live ? "Live billing has not completed activation. Checkout and portal actions are disabled until the operator completes verification." : "Test billing is not configured. Checkout and portal actions are disabled. The service operator must complete configuration and verification before testing; this is not evidence that the account has no subscription."}</p>}
    <div className="workflow-stack">
      <section className="workflow-card"><h2>Your workspace access</h2>
        {!status ? <p>Access has not loaded. Payment is not confirmed.</p> : <>
          <p data-testid="billing-access">{status.access.allowed ? "Workspace access is enabled." : "Workspace access is not enabled. Choose a plan or verify a completed checkout."}</p>
          {status.access.grant_source === "manual" && <p>Access comes from an operator allowance, not proof of payment. Billing does not change this allowance.</p>}
          <dl className="billing-details"><div><dt>AI units remaining this period</dt><dd>{status.access.period_remaining}</dd></div><div><dt>AI units remaining today (UTC)</dt><dd>{status.access.daily_remaining}</dd></div><div><dt>Access expiry</dt><dd>{dateLabel(status.access.expires_at)}</dd></div></dl>
          <p>AI actions use units, not unlimited requests. An action may need several units; access does not guarantee sufficient quota for every action.</p>
        </>}
      </section>
      <section className="workflow-card"><h2>Service-reported subscription</h2>
        {!status || status.subscription_status === "not_loaded" ? <p>Subscription status has not loaded. Do not infer free, active or cancelled status.</p> : !subscription ? <p>The service returned no subscription record. This does not establish workspace access.</p> : <dl className="billing-details">
          <div><dt>Status</dt><dd>{subscription.status}</dd></div><div><dt>Plan key</dt><dd>{subscription.plan_key || "Not supplied"}</dd></div>
          <div><dt>Period start</dt><dd>{dateLabel(subscription.period_start)}</dd></div><div><dt>Paid through{live ? "" : " (test record)"}</dt><dd>{dateLabel(subscription.paid_through)}</dd></div>
          <div><dt>Cancellation at period end</dt><dd>{subscription.cancel_at_period_end === null ? "Not supplied" : subscription.cancel_at_period_end ? "Scheduled" : "Not scheduled"}</dd></div><div><dt>Cancellation status</dt><dd>{subscription.cancellation_status || "Not supplied"}</dd></div>
        </dl>}
        {status?.reconciliation_pending && <p className="beta-notice">Billing verification is pending. Use Verify payment & access to retry; do not assume your subscription changed.</p>}
      </section>
      <section className="workflow-card"><h2>{live ? "Available plans" : "Available sandbox plans"}</h2><p>{live ? "Prices below come from the configured Stripe catalog. Confirm the final recurring amount in checkout." : "Prices are verified against the configured Stripe test catalog. Do not enter real payment details."}</p>
        <label htmlFor="billing-plan">Choose a plan</label><select id="billing-plan" value={plan} disabled={busy || !actions.checkout || uncertain || !!link} onChange={event => { setPlan(event.target.value); setLink(null); }}><option value="">Choose a plan</option>{plans.map(p => <option key={p.plan_key} value={p.plan_key}>{p.display_name} — {billingPriceLabel(p)}</option>)}</select>
        {selected && <div className="billing-price" data-testid="billing-plan-summary"><h3>{selected.display_name}</h3><p>{billingPriceLabel(selected)}</p><p>{selected.period_limit} AI units per billing period · {selected.daily_limit} per day (UTC).</p><p>Renews every {selected.interval} until cancelled. Cancel in the portal at period end; verified access continues until the paid period ends. Final checkout displays any applicable taxes. No trial or prorated plan changes are offered.</p></div>}
        {!actions.checkout && <p>New checkout is unavailable for this configuration or subscription state. Use the portal if offered, or contact the service operator.</p>}
        <button className="beta-secondary" disabled={busy || !actions.checkout || !selected || uncertain || !!link} onClick={() => void openLink("checkout")}>{live ? "Continue to secure checkout" : "Create test checkout link"}</button>
      </section>
      <section className="workflow-card"><h2>Manage or cancel</h2><p>The Stripe{live ? "" : " test"} portal opens only after an explicit request. Cancel there at period end, then return here and choose Verify payment & access. Opening the portal does not cancel a subscription.</p><button className="beta-secondary" disabled={busy || !actions.portal || !status?.account_exists || uncertain || !!link} onClick={() => void openLink("portal")}>{live ? "Manage subscription" : "Create test portal link"}</button></section>
      {link && <section className="workflow-card" role="status"><h2>{live ? "Secure link ready" : "Test link ready"}</h2><p>This destination was checked against the exact Stripe host allowlist. Nothing was opened automatically. After checkout or cancellation, return here and choose Verify payment & access. No access is granted from the return URL.</p><a className="beta-secondary billing-link" href={link.url} target="_blank" rel="noopener noreferrer">{live ? link.kind === "checkout" ? "Open Stripe checkout" : "Open Stripe portal" : link.kind === "checkout" ? "Open Stripe test checkout" : "Open Stripe test portal"} (new tab)</a></section>}
    </div>
  </section>;
}
function dateLabel(value: string | null) { if (!value) return "Not supplied"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString(); }
