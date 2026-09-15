import { useCallback, useEffect, useRef, useState } from "react";
import { mobileRequest } from "./mobile";
import { MobileApiError } from "./mobileTransport";
import type { BetaResume } from "./types";
import { checkedRecoveryOperations, missingArtifactProof, recoveryScope } from "./recoveryGate";
import type { RecoverySnapshot } from "./recoveryGate";

type Props = { userId: string; kind: "resume" | "artifact"; jobId?: string; refreshKey?: number; onPendingChange?: (pending: boolean) => void; onStatusChange?: (snapshot: RecoverySnapshot) => void; onRecovered: (result: BetaResume | { deleted: true; id: string }) => void | Promise<void> };
/** Reconciles previously persisted bytes only. No generation, upload or automatic retry. */
export function RecoveryPanel(props: Props) {
  return <RecoverySession key={recoveryScope(props.userId, props.kind, props.jobId)} {...props} />;
}
function RecoverySession({ userId, kind, jobId, refreshKey = 0, onPendingChange, onStatusChange, onRecovered }: Props) {
  const scope = recoveryScope(userId, kind, jobId);
  const [snapshot, setSnapshot] = useState<RecoverySnapshot>({ scope, phase: "checking", operations: [], missingBytes: [] });
  const current = useRef(snapshot);
  const controller = useRef(new AbortController());
  const running = useRef(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [notice, setNotice] = useState("");
  const report = useCallback((next: RecoverySnapshot) => {
    current.current = next; setSnapshot(next); onStatusChange?.(next);
    onPendingChange?.(next.phase !== "checked" || next.operations.length > 0);
  }, [onStatusChange, onPendingChange]);
  const load = useCallback(async (signal: AbortSignal) => {
    report({ ...current.current, phase: "checking" });
    try {
      const operations = checkedRecoveryOperations(await mobileRequest(userId, `/${kind}-operations`, { signal }), kind, jobId);
      if (!signal.aborted) report({ ...current.current, phase: "checked", operations, missingBytes: current.current.missingBytes.filter(id => operations.some(row => row.id === id)) });
    } catch (cause) {
      // Retain this owner's known rows for recovery; an error is never an empty list.
      if (!signal.aborted) report({ ...current.current, phase: "error" });
      throw cause;
    }
  }, [userId, kind, jobId, report]);
  useEffect(() => {
    const request = new AbortController(); controller.current = request; running.current = true; setBusy(true); setError("");
    void load(request.signal).catch(cause => { if (!request.signal.aborted) setError(cause.message); }).finally(() => { if (!request.signal.aborted) { running.current = false; setBusy(false); } });
    return () => request.abort();
  }, [load, refreshKey]);
  const act = async (operation: (signal: AbortSignal) => Promise<void>) => {
    if (running.current) return;
    const signal = controller.current.signal; running.current = true; setBusy(true); setError(""); setNotice("");
    try { await operation(signal); }
    catch (cause) { if (!signal.aborted) setError(cause instanceof Error ? cause.message : "Recovery could not complete. Refresh its status."); }
    finally { if (!signal.aborted) { running.current = false; setBusy(false); } }
  };
  return <section className="workflow-card" aria-label={`${kind} recovery`}><h3>Saved {kind} recovery</h3>
    <p>Recover an interrupted save without calling AI or generating a replacement. If recovery reports missing bytes, it cannot recreate the file.</p>
    {snapshot.phase !== "checked" && <p role="status">{snapshot.phase === "checking" ? "Checking recovery records." : "Recovery status is unknown. Refresh the records or contact the service operator."}{kind === "artifact" && " AI preparation stays blocked until recovery is verified."}</p>}
    {error && <p role="alert" className="beta-error">Recovery check: {error}</p>}{notice && <p role="status">{notice}</p>}
    <button disabled={busy} onClick={() => void act(async signal => { await load(signal); setNotice("Pending operations refreshed."); })}>Check pending {kind} operations</button>
    {snapshot.operations.length > 0 && <ul className="workflow-files">{snapshot.operations.map(operation => <li key={operation.id}><div><strong>{operation.filename}</strong><small>{operation.state} · {operation.id}</small>{snapshot.missingBytes.includes(operation.id) && <p>Recovery confirmed missing artifact bytes. This journal remains unresolved; no replacement was generated and nothing was deleted.</p>}</div><button disabled={busy} onClick={() => void act(async signal => {
      if (operation.state === "delete_pending" && !window.confirm(`Finish deleting ${operation.filename}? This resumes your previously requested deletion, not an upload.`)) return;
      const prior = current.current;
      report({ ...prior, phase: "checking", missingBytes: prior.missingBytes.filter(id => id !== operation.id) });
      try {
        const path = kind === "resume" ? `/resumes/${operation.id}/recover` : `/artifact-operations/${operation.id}/recover`;
        const result = await mobileRequest<BetaResume | { deleted: true; id: string }>(userId, path, { method: "POST", signal });
        if (signal.aborted) return;
        await onRecovered(result); await load(signal); setNotice("Recovery completed. No AI was called. Review the saved list.");
      } catch (cause) {
        if (signal.aborted) return;
        if (kind === "artifact" && cause instanceof MobileApiError && missingArtifactProof(cause, operation.id) && prior.phase === "checked") {
          report({ ...current.current, phase: "checked", missingBytes: [...current.current.missingBytes, operation.id] });
        } else report({ ...current.current, phase: "error" });
        throw cause;
      }
    })}>{operation.state === "delete_pending" ? "Finish requested deletion" : `Recover saved ${kind}`}</button></li>)}</ul>}
  </section>;
}
