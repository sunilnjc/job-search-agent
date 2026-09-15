import { useCallback, useEffect, useRef, useState } from "react";
import { mobileRequest } from "./mobile";
import { billingActions, billingCheckoutPayload, checkedBillingStatus, checkedBillingUrl } from "./billing";
import type { BillingStatus } from "./billing";
import "./studio-workflow.css";
import "./billing.css";

export function BillingPanel({ userId, onBack, onPrivacy }: { userId: string; onBack: () => void; onPrivacy: () => void }) {
  const [status, setStatus] = useState<BillingStatus | null>(null);
  const [plan, setPlan] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [link, setLink] = useState<{ kind: "checkout" | "portal"; url: string } | null>(null);
  const request = useRef<AbortController | null>(null);
  const load = useCallback(async (signal: AbortSignal) => {
    setStatus(null); setLink(null);
    const next = checkedBillingStatus(await mobileRequest(userId, "/billing", { signal }));
    if (!signal.aborted) { setStatus(next); setPlan(current => next.plan_keys.includes(current) ? current : next.plan_keys[0] ?? ""); setUncertain(false); }
  }, [userId]);
  const act = async (action: (signal: AbortSignal) => Promise<void>, write = false) => {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller; setBusy(true); setError(null);
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
  const openLink = (kind: "checkout" | "portal") => act(async signal => {
    if (!status || uncertain || (kind === "checkout" ? !actions.checkout : !actions.portal)) return;
    setLink(null);
    const body = kind === "checkout" ? billingCheckoutPayload(status, plan) : {};
    const response = await mobileRequest(userId, `/billing/${kind}`, { method: "POST", body, signal });
    const url = checkedBillingUrl(response, kind);
    if (!signal.aborted) setLink({ kind, url });
  }, true);
  const subscription = status?.subscription;
  return <section className="beta-content workflow billing-panel" aria-labelledby="billing-title">
    <button className="beta-text-button" onClick={onBack}>← Back to workspace</button>
    <header className="pursuit-page-heading"><p className="beta-eyebrow">Operator testing only</p><h1 id="billing-title">Billing · test mode</h1><p>This is a test-mode integration for operator verification, not a live subscription offer. No prices are supplied by the service and none are shown here.</p></header>
    <p>Checkout or a return URL is not proof of payment or workspace access. Access changes only after verified server reconciliation. Refresh billing to read status; this page never grants access.</p>
    <div className="workflow-actions"><button className="beta-secondary" disabled={busy} onClick={() => void act(load)}>Refresh billing</button><button className="beta-secondary" onClick={onPrivacy}>Account privacy</button></div>
    {busy && <p role="status">Contacting the authenticated billing service…</p>}
    {error && <p className="beta-error" role="alert">{error}</p>}
    {uncertain && <p className="beta-notice">No automatic retry was made. Refresh billing and check with the service operator if a checkout may already exist before requesting another link.</p>}
    {status && !status.configuration_ready && <p className="beta-notice">Test billing is not configured. Checkout and portal actions are disabled. The service operator must complete configuration and verification before testing; this is not evidence that the account has no subscription.</p>}
    <div className="workflow-stack">
      <section className="workflow-card"><h2>Service-reported subscription</h2>
        {!status || status.subscription_status === "not_loaded" ? <p>Subscription status has not loaded. Do not infer free, active or cancelled status.</p> : !subscription ? <p>The service returned no subscription record. This does not establish workspace access.</p> : <dl className="billing-details">
          <div><dt>Status</dt><dd>{subscription.status}</dd></div><div><dt>Plan key</dt><dd>{subscription.plan_key || "Not supplied"}</dd></div>
          <div><dt>Period start</dt><dd>{dateLabel(subscription.period_start)}</dd></div><div><dt>Paid through (test record)</dt><dd>{dateLabel(subscription.paid_through)}</dd></div>
          <div><dt>Cancellation at period end</dt><dd>{subscription.cancel_at_period_end === null ? "Not supplied" : subscription.cancel_at_period_end ? "Scheduled" : "Not scheduled"}</dd></div><div><dt>Cancellation status</dt><dd>{subscription.cancellation_status || "Not supplied"}</dd></div>
        </dl>}
      </section>
      <section className="workflow-card"><h2>Stripe test checkout</h2><p>Plan keys below come from the service; they are not prices or commercial plan descriptions. Only the invited test account is used. Do not enter real payment details.</p>
        <label>Server-configured test plan<select value={plan} disabled={busy || !actions.checkout || uncertain || !!link} onChange={event => { setPlan(event.target.value); setLink(null); }}><option value="">Choose a test plan</option>{status?.plan_keys.map(key => <option key={key} value={key}>{key}</option>)}</select></label>
        {!actions.checkout && <p>New checkout is unavailable for this configuration or subscription state. Use the portal if offered, or contact the service operator.</p>}
        <button className="beta-secondary" disabled={busy || !actions.checkout || !plan || uncertain || !!link} onClick={() => void openLink("checkout")}>Create test checkout link</button>
      </section>
      <section className="workflow-card"><h2>Manage test billing</h2><p>The Stripe portal opens only after an explicit request. Any cancellation is handled there and must be confirmed by the service; opening the portal does not cancel a subscription.</p><button className="beta-secondary" disabled={busy || !actions.portal || uncertain || !!link} onClick={() => void openLink("portal")}>Create test portal link</button></section>
      {link && <section className="workflow-card" role="status"><h2>Test link ready</h2><p>This API-returned destination was checked against the exact Stripe host allowlist. The link was not opened automatically. After operator testing, return here and use Refresh billing; no access is granted from the return URL.</p><a className="beta-secondary billing-link" href={link.url} target="_blank" rel="noopener noreferrer">{link.kind === "checkout" ? "Open Stripe test checkout" : "Open Stripe test portal"} (new tab)</a></section>}
    </div>
  </section>;
}
function dateLabel(value: string | null) { if (!value) return "Not supplied"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString(); }
