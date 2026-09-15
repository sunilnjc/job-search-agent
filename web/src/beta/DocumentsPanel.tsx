import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import type { BetaResume } from "./types";
import { downloadMobileFile, loadMobileWorkspace, mobileRequest, uploadMobileResume } from "./mobile";
import { MobileApiError, validateResume } from "./mobileTransport";
import { RecoveryPanel } from "./RecoveryPanel";

export function DocumentsPanel({ session, onChanged }: { session: Session; onChanged?: () => void | Promise<void> }) {
  const [resumes, setResumes] = useState<BetaResume[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reviewText, setReviewText] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [uncertainUpload, setUncertainUpload] = useState(false);
  const request = useRef(new AbortController());
  const input = useRef<HTMLInputElement>(null);
  const uploadKeys = useRef(new Map<string, string>());
  const [recoveryKey, setRecoveryKey] = useState(0);
  const userId = session.user.id;
  const load = useCallback(async () => {
    const data = await loadMobileWorkspace(userId, request.current.signal);
    if (!request.current.signal.aborted) setResumes(data.resumes);
    return data;
  }, [userId]);
  useEffect(() => {
    const controller = new AbortController(); request.current = controller;
    void load().catch(cause => { if (!controller.signal.aborted) setError(cause.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [load]);
  const act = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await action(); }
    catch (cause) { if (!request.current.signal.aborted) setError(cause instanceof Error ? cause.message : "The request could not be completed."); }
    finally { if (!request.current.signal.aborted) setBusy(false); }
  };
  const upload = (file: File) => act(async () => {
    validateResume(file);
    try {
      const fingerprint = file.name + ":" + Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))).map(byte => byte.toString(16).padStart(2, "0")).join("");
      const key = uploadKeys.current.get(fingerprint) ?? crypto.randomUUID();
      uploadKeys.current.set(fingerprint, key);
      await uploadMobileResume(userId, file, request.current.signal, key);
      uploadKeys.current.delete(fingerprint);
      setNotice("Resume validated and uploaded. Review extracted facts before using them for preparation.");
      await load(); await onChanged?.();
    } catch (cause) {
      if (cause instanceof MobileApiError && (cause.uncertain || cause.status >= 500)) setUncertainUpload(true);
      setRecoveryKey(current => current + 1);
      throw cause;
    } finally { if (input.current) input.current.value = ""; }
  });
  const confirmFacts = () => act(async () => {
    if (!confirmed || !reviewText?.trim()) throw new Error("Review the extracted text and explicitly confirm its accuracy.");
    // Merge only explicitly reviewed text into fresh context; never replace native structured qualifications.
    const current = await load();
    const existing = current.profile?.career_text?.trim() ?? "";
    const addition = reviewText.trim();
    const careerText = existing.includes(addition) ? existing : [existing, addition].filter(Boolean).join("\n\n");
    if (careerText.length > 100000) throw new Error("Combined career facts exceed 100,000 characters. Edit your profile to shorten them first.");
    await mobileRequest(userId, "/profile", { method: "PUT", signal: request.current.signal, body: { career_text: careerText } });
    setReviewText(null); setConfirmed(false); setNotice("Your reviewed career facts were saved as self-reported information."); await onChanged?.();
  });
  return <section className="beta-documents-panel workflow" aria-label="Source resumes">
    <div className="beta-documents-panel__header"><div><p className="beta-eyebrow">Private documents</p><h2>Your source resumes</h2><p>PDF or DOCX, up to 8 MiB. Uploading does not start AI or send anything to an employer. Select a source explicitly in each role’s Studio.</p></div>
      <button type="button" className="beta-secondary" onClick={() => input.current?.click()} disabled={busy || uncertainUpload}>Upload resume</button>
      <input ref={input} aria-label="Source resume file" className="beta-visually-hidden" type="file" accept=".pdf,.docx" disabled={busy || uncertainUpload} onChange={event => { const file = event.target.files?.[0]; if (file) void upload(file); }} />
    </div>
    {error && <p className="beta-error" role="alert">{error}</p>}{notice && <p className="beta-notice" role="status">{notice}</p>}
    {uncertainUpload && <p className="beta-notice">Upload outcome is uncertain. Refresh and inspect the saved list before selecting the file again. <button disabled={busy} onClick={() => void act(async () => { await load(); setUncertainUpload(false); setNotice("Saved resumes refreshed. Check filenames before choosing to upload again."); })}>Refresh saved resumes</button></p>}
    {loading ? <p>Loading private resumes…</p> : resumes.length === 0 ? <p>No saved resume yet. Upload one here to enable document preparation.</p> : <ul className="beta-documents-list">{resumes.map(resume => <li key={resume.id} className="beta-documents-list__item">
      <div><strong>{resume.label}</strong><p>{resume.original_filename} · {(resume.byte_size / 1024).toFixed(0)} KiB{resume.is_default ? " · Default on mobile" : ""}</p></div>
      <div className="beta-documents-list__actions">
        <button disabled={busy} onClick={() => void act(() => downloadMobileFile(userId, "resumes", resume.id, resume.original_filename, request.current.signal))}>Download source</button>
        <button disabled={busy} onClick={() => void act(async () => { const result = await mobileRequest<{ text: string }>(userId, `/resumes/${resume.id}/text`, { signal: request.current.signal }); setReviewText(result.text); setConfirmed(false); })}>Review extracted facts</button>
        <button disabled={busy} onClick={() => { if (window.confirm(`Delete ${resume.original_filename}? The source file is removed; generated artifacts are retained. Restore by uploading your original again.`)) void act(async () => { await mobileRequest(userId, `/resumes/${resume.id}`, { method: "DELETE", signal: request.current.signal }); await load(); await onChanged?.(); setNotice("Source resume deleted. Generated artifacts were retained."); }); }}>Delete source</button>
      </div>
    </li>)}</ul>}
    {!loading && <button className="beta-text-button" disabled={busy} onClick={() => void act(async () => { await load(); })}>Refresh resumes</button>}
    <RecoveryPanel userId={userId} kind="resume" refreshKey={recoveryKey} onRecovered={async () => { await load(); setUncertainUpload(false); await onChanged?.(); }} />
    {reviewText !== null && <div className="workflow-card">
      <h3>Confirm your career facts</h3><p>Extraction is unverified. Correct dates, credentials and achievements before saving. Only text you confirm is added to your career facts; this does not certify work rights.</p>
      <label>Extracted career text<textarea rows={10} maxLength={100000} value={reviewText} disabled={busy} onChange={event => { setReviewText(event.target.value); setConfirmed(false); }} /></label>
      <label className="workflow-check"><input type="checkbox" checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)} /><span>I reviewed this text and confirm it accurately describes my career.</span></label>
      <button disabled={busy || !confirmed || !reviewText.trim()} className="beta-primary" onClick={() => void confirmFacts()}>Save confirmed facts</button>
      <button disabled={busy} onClick={() => { setReviewText(null); setConfirmed(false); }}>Cancel review</button>
    </div>}
  </section>;
}
