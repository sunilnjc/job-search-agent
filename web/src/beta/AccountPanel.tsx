import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { downloadAccountExport, mobileRequest } from "./mobile";
import { MobileApiError } from "./mobileTransport";
import { canDownloadExport, checkedAccountPrivacy, checkedPrivacyRequest, ERASURE_CONFIRMATION, erasurePayload, pendingPrivacyRequest, privacyStateGuidance } from "./accountPrivacy";
import type { AccountPrivacy, PrivacyRequest } from "./accountPrivacy";
import "./studio-workflow.css";
import "./account-privacy.css";

/** Auth-only privacy surface. Never depends on bootstrap, entitlement or an AI budget. */
type Props = { session: Session; onBack: () => void };
export function AccountPanel(props: Props) {
  // Keep privacy isolation self-contained even if a future caller is not keyed.
  return <AccountSession key={props.session.user.id} {...props} />;
}
function AccountSession({ session, onBack }: Props) {
  const [account, setAccount] = useState<AccountPrivacy | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [reviewingErasure, setReviewingErasure] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const controller = useRef(new AbortController());
  const busyRef = useRef(false);
  const userId = session.user.id;
  const verifiedEmail = session.user.email && (session.user.email_confirmed_at || session.user.confirmed_at) ? session.user.email : null;
  const load = useCallback(async (signal: AbortSignal) => {
    const data = checkedAccountPrivacy(await mobileRequest(userId, "/account", { signal }));
    if (!signal.aborted) { setAccount(data); setUncertain(false); }
  }, [userId]);
  useEffect(() => {
    const request = new AbortController(); controller.current = request;
    void load(request.signal).catch(cause => { if (!request.signal.aborted) setError(cause.message); }).finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [load]);
  const act = async (operation: (signal: AbortSignal) => Promise<void>, writing = false) => {
    if (busyRef.current) return;
    const signal = controller.current.signal;
    busyRef.current = true; setBusy(true); setError(""); setNotice("");
    try { await operation(signal); }
    catch (cause) {
      if (!signal.aborted) {
        setError(cause instanceof Error ? cause.message : "The account request could not be completed. Refresh requests before trying again.");
        if (writing && (!(cause instanceof MobileApiError) || cause.uncertain || cause.status >= 500)) setUncertain(true);
      }
    } finally { busyRef.current = false; if (!signal.aborted) setBusy(false); }
  };
  const resetErasure = () => { setReviewingErasure(false); setConfirmation(""); setAcknowledged(false); };
  const recordRequest = (value: unknown) => {
    const item = checkedPrivacyRequest(value);
    setAccount(current => current ? { ...current, requests: [item, ...current.requests.filter(request => request.id !== item.id)] } : current);
    return item;
  };
  const exportPending = account?.requests.some(request => request.kind === "export" && pendingPrivacyRequest(request));
  const erasurePending = account?.requests.some(request => request.kind === "erase" && pendingPrivacyRequest(request));
  const erasureComplete = account?.requests.some(request => request.kind === "erase" && request.state === "complete");
  const disabled = busy || loading || uncertain || !account || !account.capability.processing_configured;
  const requestExport = () => act(async signal => {
    if (disabled || !account?.capability.export || exportPending || erasurePending || erasureComplete) return;
    recordRequest(await mobileRequest(userId, "/account/exports", { method: "POST", body: {}, signal }));
    setNotice("Export request recorded by the service. Its status is shown below; a download is available only when the service reports it ready.");
  }, true);
  const queueErasure = () => act(async signal => {
    if (disabled || !account?.capability.erasure || !reviewingErasure || erasurePending || erasureComplete) return;
    const body = erasurePayload(confirmation, acknowledged, verifiedEmail);
    recordRequest(await mobileRequest(userId, "/account/erasure", { method: "POST", body, signal }));
    resetErasure(); setNotice("Erasure request recorded by the service. Accepted erasure freezes workspace access immediately and cannot be cancelled here. This is not confirmation that your account or data has been erased. Use Refresh requests to check processing.");
  }, true);
  const download = (request: PrivacyRequest) => act(async signal => {
    if (!canDownloadExport(request)) return;
    await downloadAccountExport(userId, request.id, signal);
    if (!signal.aborted) setNotice("Your export download was requested. Keep the downloaded file private and secure.");
  });
  return <section className="beta-content workflow account-privacy" aria-labelledby="account-privacy-title">
    <button className="beta-text-button" onClick={onBack}>← Back to workspace</button>
    <header className="pursuit-page-heading"><p className="beta-eyebrow">Your account</p><h1 id="account-privacy-title">Account privacy</h1><p>Request an export or account erasure and track the service’s response. Privacy controls are separate from paid workspace access and AI quotas.</p></header>
    <p>Signed in as <strong>{session.user.email || "an authenticated account"}</strong>. The service verifies ownership of every request.</p>
    <button className="beta-secondary" disabled={busy || loading} onClick={() => void act(async signal => { await load(signal); resetErasure(); setNotice("Privacy requests refreshed from the service."); })}>Refresh requests</button>
    {loading && <p role="status">Loading privacy request status…</p>}
    {busy && <p role="status">Contacting the private account service…</p>}
    {error && <p role="alert" className="beta-error">{error}</p>}
    {notice && <p role="status" className="beta-notice">{notice}</p>}
    {uncertain && <p className="beta-notice">The last request may have been accepted. Refresh requests before making another request. No automatic retry was made.</p>}
    {account && !account.capability.processing_configured && <p className="beta-notice">Processing is unavailable or not configured. New requests are disabled here until the service operator restores privacy processing. Existing requests may still be queued or processing, including requests whose acknowledgement was lost. This status does not establish whether erasure has started or finished. Inspect Request history, use Refresh requests, and contact the service operator for help. Existing request status and ready downloads remain accessible.</p>}
    <div className="workflow-stack">
      <section className="workflow-card" aria-labelledby="account-export-title"><h2 id="account-export-title">Export your data</h2><p>The service prepares your export after a request is accepted. This page does not claim an archive exists until its status is complete and download-ready.</p>
        {account && !account.capability.export && <p>Export requests are not currently offered by this service.</p>}
        {exportPending && <p>An export request is already pending. Check its status below.</p>}
        <button className="beta-secondary" disabled={disabled || !account?.capability.export || exportPending || erasurePending || erasureComplete} onClick={() => void requestExport()}>Request data export</button>
      </section>
      <section className="workflow-card account-erasure" aria-labelledby="account-erasure-title"><h2 id="account-erasure-title">Account erasure</h2><p><strong>Request and download an export first if you want a copy.</strong> An accepted erasure request freezes workspace access immediately and cannot be cancelled in this UI. Do not queue erasure while waiting for an export you still want to download.</p><p>Erasure is processed later by the service. Active subscription cancellation must be confirmed before the worker deletes account data. Once processed, erased data may not be recoverable. Follow service messages for processing scope and any retained data; queue acceptance is not proof of completed deletion.</p>
        {account && !account.capability.erasure && <p>Erasure requests are not currently offered by this service.</p>}
        {!verifiedEmail && <p>A verified session email is required to request erasure. Sign in again with your verified account; a manually entered email cannot establish ownership.</p>}
        {erasurePending && <p>An erasure request is already pending and workspace access is frozen. Refresh its status below; this UI cannot cancel it or request another export.</p>}
        {erasureComplete && <p>The service reports an erasure request complete. See its message below before taking any further account action.</p>}
        {!reviewingErasure ? <button className="beta-secondary" disabled={disabled || !account?.capability.erasure || !verifiedEmail || erasurePending || erasureComplete} onClick={() => { setReviewingErasure(true); setConfirmation(""); setAcknowledged(false); }}>Review account erasure</button>
          : <form onSubmit={event => { event.preventDefault(); void queueErasure(); }}><p><strong>Step 2 of 2: confirm your request</strong></p><p>This request applies to the verified session email: <strong>{verifiedEmail}</strong>.</p>
            <label>Type DELETE MY ACCOUNT<input autoComplete="off" spellCheck={false} maxLength={40} value={confirmation} disabled={busy} onChange={event => { setConfirmation(event.target.value); setAcknowledged(false); }} /></label>
            <label className="workflow-check"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={event => setAcknowledged(event.target.checked)} /><span>I understand this queues an erasure request that immediately freezes workspace access, cannot be cancelled here, and may lead to irreversible deletion. I have downloaded any export I want to keep. Queueing is not proof of completed deletion.</span></label>
            <div className="workflow-actions"><button className="beta-secondary" type="button" disabled={busy} onClick={resetErasure}>Go back without requesting erasure</button><button className="beta-primary" disabled={disabled || confirmation !== ERASURE_CONFIRMATION || !acknowledged || !verifiedEmail || erasurePending}>Queue erasure request</button></div>
          </form>}
      </section>
      <section className="workflow-card" aria-labelledby="account-requests-title"><h2 id="account-requests-title">Request history</h2><p>Status comes from the service. Use Refresh requests to update it; this page never polls or retries erasure automatically.</p>
        {!account ? <p>Request history has not loaded. Refresh requests to reconnect.</p> : account.requests.length === 0 ? <p>No privacy requests were returned.</p> : <ol className="account-request-list">{account.requests.map(request => <li key={request.id} className="account-request" aria-label={`${request.kind === "export" ? "Export" : "Erasure"} request ${request.id}`}>
          <div className="account-request-heading"><h3>{request.kind === "export" ? "Data export" : "Account erasure"}</h3><span className="account-state">{request.state}</span></div>
          <p>{privacyStateGuidance(request)}</p>{request.message && <p className="account-server-message"><strong>Service message:</strong> {request.message}</p>}
          <dl><div><dt>Request ID</dt><dd>{request.id}</dd></div><div><dt>Created</dt><dd>{formatDate(request.created_at)}</dd></div>{request.completed_at && <div><dt>Service completion time</dt><dd>{formatDate(request.completed_at)}</dd></div>}{request.expires_at && <div><dt>Download expires</dt><dd>{formatDate(request.expires_at)}</dd></div>}{request.error_code && <div><dt>Service code</dt><dd>{request.error_code}</dd></div>}</dl>
          {canDownloadExport(request) && <button className="beta-secondary" disabled={busy} onClick={() => void download(request)}>Download account export</button>}
        </li>)}</ol>}
      </section>
    </div>
  </section>;
}
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "Not available" : date.toLocaleString(); }
