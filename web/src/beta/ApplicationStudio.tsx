import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import type { BetaApplication, BetaArtifact, BetaJob, BetaQuestion, BetaReadiness, BetaWorkspace } from "./types";
import { downloadMobileFile, loadMobileWorkspace, mobileRequest } from "./mobile";
import { MobileApiError } from "./mobileTransport";
import { DocumentsPanel } from "./DocumentsPanel";
import { RecoveryPanel } from "./RecoveryPanel";
import { allMissingBytes, checkedRecoveryOperations, newPreparationAcknowledged, operationSet, recoveryClear, recoveryScope } from "./recoveryGate";
import type { RecoverySnapshot } from "./recoveryGate";
import { safePostingUrl } from "./workspace";
import { checkedReadiness, durablePacketReviewed, latestApplication, packetReview, readinessIssue, readinessReadIssue, refreshAfterApplicationSave, reviewMatches, serverStudioPackets, studioContextKey, studioQuestions } from "./studioReadiness";
import { DocumentReviewPanel } from "./DocumentReviewPanel";
import { assessmentDisplay } from "./assessmentDisplay";
import { FitExplanationBlock } from "./FitExplanationBlock";
import type { PacketReview } from "./studioReadiness";
import "./studio-workflow.css";

type Step = "overview" | "documents" | "questions" | "review";
type Props = { session: Session; job: BetaJob; onApplicationStatusChange?: (application: BetaApplication) => void | Promise<void> };
const STEPS: Array<[Step, string]> = [["overview", "Role & eligibility"], ["documents", "Documents"], ["questions", "Questions"], ["review", "Final review"]];
function documentLabel(artifact: BetaArtifact) {
  const kind = ({ tailored_resume: "Tailored resume", cover_letter: "Cover letter", answer_packet: "Answer packet" } as Record<string, string>)[artifact.kind] ?? "Application document";
  const format = artifact.filename.toLowerCase().endsWith(".docx") ? "DOCX" : artifact.filename.toLowerCase().endsWith(".pdf") ? "PDF" : "File";
  return `${kind} · ${format}`;
}

export function ApplicationStudio(props: Props) {
  return <StudioSession key={`${props.session.user.id}:${props.job.id}`} {...props} />;
}
function StudioSession({ session, job: summaryJob, onApplicationStatusChange }: Props) {
  const userId = session.user.id;
  const scope = recoveryScope(userId, "artifact", summaryJob.id);
  const [job, setJob] = useState<BetaJob | null>(null);
  const [workspace, setWorkspace] = useState<BetaWorkspace | null>(null);
  const [step, setStep] = useState<Step>("overview");
  const [description, setDescription] = useState("");
  const [resumeId, setResumeId] = useState("");
  const [variant, setVariant] = useState("role_aligned");
  const [consent, setConsent] = useState(false);
  const [review, setReview] = useState<PacketReview | null>(null);
  const [packetKey, setPacketKey] = useState("");
  const [snapshotVerified, setSnapshotVerified] = useState(false);
  const [serverReadiness, setServerReadiness] = useState<BetaReadiness | null>(null);
  const [readinessError, setReadinessError] = useState("");
  const [acknowledgedApplication, setAcknowledgedApplication] = useState<BetaApplication | null>(null);
  const [eligibility, setEligibility] = useState<"" | "eligible" | "ineligible" | "unknown">("");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [notes, setNotes] = useState("");
  const [submittedConfirmed, setSubmittedConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [newQuestions, setNewQuestions] = useState<BetaQuestion[]>([]);
  const [preparedIds, setPreparedIds] = useState<string[]>([]);
  const [syncWarning, setSyncWarning] = useState("");
  const [aiHold, setAiHold] = useState(false);
  const [recovery, setRecovery] = useState<RecoverySnapshot>({ scope, phase: "checking", operations: [], missingBytes: [] });
  const [freshSourceId, setFreshSourceId] = useState<string | null>(null);
  const [acknowledgedOperations, setAcknowledgedOperations] = useState<string[]>([]);
  const [recoveryKey, setRecoveryKey] = useState(0);
  const request = useRef(new AbortController());
  const receiveRecovery = useCallback((next: RecoverySnapshot) => {
    if (next.scope !== scope) return;
    setRecovery(next);
    // Consent is session-local and tied to this verified operation set. Every new
    // check (including new pending work or a failed check) requires fresh consent.
    setFreshSourceId(null); setAcknowledgedOperations([]);
  }, [scope]);
  const load = useCallback(async () => {
    setSnapshotVerified(false); setReview(null); setServerReadiness(null); setReadinessError("");
    const signal = request.current.signal;
    const [data, detail, readiness] = await Promise.all([
      loadMobileWorkspace(userId, signal),
      mobileRequest<BetaJob>(userId, `/jobs/${summaryJob.id}`, { signal }),
      mobileRequest<unknown>(userId, `/jobs/${summaryJob.id}/readiness`, { signal })
        .then(value => checkedReadiness(value, userId, summaryJob.id))
        .catch(cause => {
          if (!signal.aborted) setReadinessError(cause instanceof Error ? cause.message : "Current packet readiness could not be checked.");
          return null;
        }),
    ]);
    if (signal.aborted) return;
    setWorkspace(data); setJob(detail); setDescription(detail.description ?? ""); setServerReadiness(readiness);
    setResumeId(current => data.resumes.some(resume => resume.id === current) ? current : data.resumes.find(resume => resume.is_default)?.id ?? data.resumes[0]?.id ?? "");
    setPacketKey(current => readiness?.packets.some(p => p.key === current) ? current : readiness?.packets.find(p => durablePacketReviewed(readiness, p))?.key ?? "");
    setNewQuestions([]);
    setSnapshotVerified(true);
    return { workspace: data, job: detail, readiness };
  }, [userId, summaryJob.id]);
  useEffect(() => {
    const controller = new AbortController(); request.current = controller;
    void load().catch(cause => { if (!controller.signal.aborted) setError(cause.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [load]);
  const invalidateReview = () => { setReview(null); setPreparedIds([]); };
  const act = async (operation: () => Promise<void>) => {
    if (busy) return;
    const signal = request.current.signal;
    setBusy(true); setError(""); setNotice("");
    try { await operation(); }
    catch (cause) {
      if (signal.aborted) return;
      setError(cause instanceof Error ? cause.message : "The request could not be completed.");
      if (cause instanceof MobileApiError && (cause.code?.includes("artifact_") || cause.uncertain)) {
        setAiHold(true); setStep("documents"); setRecoveryKey(current => current + 1);
        setSyncWarning("An operation may already have saved documents. Check saved files and recovery below before considering another generation. Recovery does not call AI.");
      }
      if (cause instanceof MobileApiError && cause.questions.length) { setNewQuestions(cause.questions); setStep("questions"); }
    } finally { if (!signal.aborted) setBusy(false); }
  };
  const refresh = () => act(async () => { invalidateReview(); await load(); setAiHold(false); setRecoveryKey(current => current + 1); setNotice("Saved workspace refreshed. Check saved documents and pending operations before choosing any new generation. Unsaved role edits were replaced with the saved version."); });
  if (loading) return <section className="workflow" aria-busy="true"><h2>Loading Application Studio…</h2></section>;
  if (!job || !workspace) return <section className="workflow"><h2>Application Studio could not load</h2><p role="alert">{error}</p><button disabled={busy} onClick={() => void refresh()}>Retry Studio</button></section>;
  const application = latestApplication(workspace.applications, job.id, acknowledgedApplication);
  const artifacts = workspace.artifacts.filter(item => item.job_id === job.id);
  const questions = studioQuestions(workspace, job.id, newQuestions);
  const pending = questions.filter(item => item.status !== "answered");
  const selectedResume = workspace.resumes.find(item => item.id === resumeId);
  const eligible = job.eligibility_review?.status === "eligible" && job.eligibility_review.confirmed === true;
  const dirty = description.trim() !== (job.description ?? "").trim();
  const analysisContextReady = Boolean(consent && selectedResume && workspace.profile?.career_text?.trim() && description.trim() && !dirty);
  const missingBytesConfirmed = allMissingBytes(recovery, scope);
  const newPreparationApproved = newPreparationAcknowledged(recovery, scope, resumeId, freshSourceId, workspace.resumes.map(resume => resume.id), acknowledgedOperations);
  const canAnalyze = analysisContextReady && !aiHold && recoveryClear(recovery, scope);
  const canPrepare = analysisContextReady && ((!aiHold && recoveryClear(recovery, scope)) || newPreparationApproved);
  const packets = serverStudioPackets(serverReadiness, resumeId, variant);
  const packet = packets.find(item => item.key === packetKey);
  const contextKey = studioContextKey(userId, workspace, job, resumeId, variant);
  const durableReviewed = Boolean(packet?.server && durablePacketReviewed(serverReadiness, packet.server));
  const reviewed = reviewMatches(review, packet, contextKey) || durableReviewed;
  const readIssue = readinessReadIssue(workspace);
  const packetIssue = !serverReadiness ? "Current server readiness is unavailable. Documents can still be downloaded and external submissions recorded; Ready is blocked until verification succeeds."
    : serverReadiness.reason === "too_many_records" ? "This role's generation history exceeds the verified limit. Contact support before marking Ready."
    : readinessIssue(workspace, job, resumeId, packet);
  const hasDocuments = Boolean(packet?.pair && !packet.issue);
  const canReady = Boolean(snapshotVerified && !packetIssue && reviewed && !pending.length && !dirty);
  const postingUrl = safePostingUrl(job.source_url);
  const saveApplication = (status: BetaApplication["status"]) => act(async () => {
    if (status === "ready" && !canReady) throw new Error("Review eligibility, questions and documents before marking ready.");
    if (status === "submitted" && (!submittedConfirmed || !notes.trim())) throw new Error("Confirm your external submission and add a record note.");
    let result: BetaApplication;
    if (status === "ready") {
      // Refresh at the click boundary; the API then verifies stored bytes and SQL
      // atomically compares this generation fingerprint before saving its receipt.
      const fresh = await load();
      if (!fresh) return;
      const freshPacket = serverStudioPackets(fresh.readiness, resumeId, variant).find(item => item.key === packetKey);
      const issue = readinessIssue(fresh.workspace, fresh.job, resumeId, freshPacket);
      const stillReviewed = reviewMatches(review, freshPacket, studioContextKey(userId, fresh.workspace, fresh.job, resumeId, variant))
        || Boolean(durableReviewed && freshPacket?.server && durablePacketReviewed(fresh.readiness, freshPacket.server));
      if (issue || !stillReviewed || !freshPacket?.server?.current || !freshPacket.pair) {
        throw new Error(issue ?? "The career facts, preferences, role, answers or selected packet changed. Your review was cleared; no Ready record was saved. Review the saved packet against the current facts again.");
      }
      const saved = await mobileRequest<{ application: BetaApplication; readiness: BetaReadiness }>(userId, `/jobs/${job.id}/review-packet`, {
        method: "POST", signal: request.current.signal,
        body: { run_id: freshPacket.server.run_id, resume_artifact_id: freshPacket.pair[0].id, letter_artifact_id: freshPacket.pair[1].id,
          packet_fingerprint: freshPacket.server.packet_fingerprint, confirmed: true },
      });
      const receipt = checkedReadiness(saved.readiness, userId, job.id);
      result = { ...saved.application, status: receipt.application_status, recorded_status: receipt.recorded_status ?? saved.application.status, readiness: receipt };
    } else {
      result = await mobileRequest<BetaApplication>(userId, "/applications", { method: "POST", signal: request.current.signal, body: { job_id: job.id, status, notes: status === "submitted" ? notes.trim() : application?.notes ?? null } });
    }
    setAcknowledgedApplication(result);
    const refreshes = await refreshAfterApplicationSave(load, async () => { await onApplicationStatusChange?.(result); });
    const saved = status === "submitted" ? "Recorded as submitted by you. This app did not submit or verify an application."
      : status === "ready" && result.status !== "ready" ? "Your review was saved, but newer changes have already invalidated readiness. Refresh and review the current context. Nothing was sent."
      : `Your ${status} application record was saved. Nothing was sent to an employer.`;
    setNotice(saved + (refreshes.some(outcome => outcome.status === "rejected") ? " A workspace refresh failed; Discover or Tracker may still show an older state. Refresh the workspace; do not repeat the completed save." : ""));
  });
  const analyze = (kind: "rank" | "prepare") => act(async () => {
    if (kind === "prepare" ? !canPrepare : !canAnalyze) throw new Error("Save the job description, select a resume, confirm your career facts, enable AI assistance and review pending recovery before continuing.");
    const recoveryAtClick = recovery;
    const approvedAtClick = kind === "prepare" && newPreparationApproved;
    setFreshSourceId(null); setAcknowledgedOperations([]);
    setRecovery(current => ({ ...current, phase: "checking" }));
    // Recheck at the spend boundary: a newly observed operation invalidates the
    // acknowledgement before any AI endpoint is called. Never cancel its journal.
    try {
      const operations = checkedRecoveryOperations(await mobileRequest(userId, "/artifact-operations", { signal: request.current.signal }), "artifact", summaryJob.id);
      if (operationSet(operations) !== operationSet(recoveryAtClick.operations)) {
        receiveRecovery({ scope, phase: "checked", operations, missingBytes: [] });
        setRecoveryKey(current => current + 1);
        throw new Error("Pending recovery changed. No AI was called. Review every current operation, then select a source and acknowledge any new paid preparation again.");
      }
      if (operations.length && !approvedAtClick) throw new Error("Recovery remains unresolved. No AI was called.");
    } catch (cause) {
      setRecovery(current => ({ ...current, phase: "error" }));
      throw cause;
    }
    setAiHold(true);
    invalidateReview();
    try {
    if (kind === "rank") {
      const result = await mobileRequest<{ operation_status?: string; warnings?: string[] }>(userId, `/jobs/${job.id}/rank`, { method: "POST", signal: request.current.signal, timeoutMs: 180000, body: { resume_id: resumeId } });
      const syncPending = result.operation_status === "saved_sync_pending" || Boolean(result.warnings?.length);
      setAiHold(syncPending);
      if (syncPending) setSyncWarning(result.warnings?.join(" ") || "Ranking saved; activity synchronization is pending. Refresh before considering another assessment.");
      await load(); setNotice("Role assessment saved. This is an AI estimate, not a hiring prediction or work-rights verification.");
    } else {
      const result = await mobileRequest<{ artifacts: BetaArtifact[]; questions: BetaQuestion[]; operation_status?: string; warnings?: string[] }>(userId, `/jobs/${job.id}/prepare`, { method: "POST", signal: request.current.signal, timeoutMs: 180000, body: { resume_id: resumeId, variant } });
      const syncPending = result.operation_status === "saved_sync_pending" || Boolean(result.warnings?.length);
      setAiHold(syncPending);
      if (syncPending) setSyncWarning(result.warnings?.join(" ") || "Documents saved; activity synchronization is pending. Refresh before considering another generation.");
      const fresh = await load(); setPreparedIds(result.artifacts.map(item => item.id)); setNewQuestions(result.questions ?? []);
      const generatedPacket = serverStudioPackets(fresh?.readiness ?? null, resumeId, variant)
        .filter(p => p.pair?.every(a => result.artifacts.some(created => created.id === a.id)));
      setPacketKey(generatedPacket[0]?.key ?? "");
      setNotice("Draft documents prepared. Download and review every claim before applying externally.");
      if (result.questions?.some(item => item.status !== "answered")) setStep("questions");
    }
    } finally { setRecoveryKey(current => current + 1); }
  });
  return <section className="beta-application-studio workflow" aria-label="Application Studio">
    <header className="beta-application-studio__header"><p className="beta-eyebrow">Application Studio · manual preparation</p><h2>{job.title}</h2><p>{job.company_name} · {job.location_text || "Location not listed"}</p><p>Application record: {serverReadiness?.application_id ? serverReadiness.application_status : application?.status ?? "Not created"}. Ready requires a current reviewed packet. Submitted is user-reported, not verified by an employer. No automatic submission.</p>{postingUrl && <a href={postingUrl} target="_blank" rel="noreferrer">Open original posting ↗</a>}</header>
    <nav className="beta-application-studio__steps" aria-label="Application preparation steps">{STEPS.map(([id, label]) => <button key={id} aria-current={step === id ? "step" : undefined} className={step === id ? "is-active" : ""} onClick={() => setStep(id)}>{label}{id === "questions" && pending.length ? ` (${pending.length})` : ""}</button>)}</nav>
    {error && <p className="beta-error" role="alert">{error}</p>}{notice && <p className="beta-notice" role="status">{notice}</p>}
    {syncWarning && <p className="beta-notice" role="status">{syncWarning}</p>}
    {busy && <p role="status">Working… AI preparation can take a few minutes. Closing this window does not guarantee cancellation on the server.</p>}
    <button className="beta-text-button" disabled={busy} onClick={() => void refresh()}>Refresh saved Studio</button>
    {step === "overview" && <div className="workflow-stack">
      <form className="workflow-card" onSubmit={event => { event.preventDefault(); void act(async () => {
        if (!description.trim()) throw new Error("Paste the full job description.");
        await mobileRequest(userId, `/jobs/${job.id}`, { method: "PATCH", signal: request.current.signal, body: { description: description.trim() } });
        invalidateReview(); setConfirmed(false); setEligibility(""); setReason(""); await load(); setNotice("Job description saved. Changed role facts invalidate the previous match and eligibility review; old documents remain for your review.");
      }); }}>
        <h3>Job description</h3><p>Paste the employer’s full requirements. We do not fetch this URL or verify whether the vacancy is still open.</p>
        <label>Full job description<textarea required maxLength={80000} rows={10} value={description} disabled={busy} onChange={event => { setDescription(event.target.value); invalidateReview(); }} /></label>
        <button className="beta-secondary" disabled={busy || !dirty || !description.trim()}>Save job description</button>
      </form>
      <form className="workflow-card" onSubmit={event => { event.preventDefault(); void act(async () => {
        if (!eligibility || !confirmed || !reason.trim()) throw new Error("Choose a status, give a reason and explicitly confirm your self-report.");
        await mobileRequest(userId, `/jobs/${job.id}/eligibility`, { method: "POST", signal: request.current.signal, body: { status: eligibility, reason: reason.trim(), confirmed: true } });
        invalidateReview(); await load(); setConfirmed(false); setNotice("Your job-specific self-report was saved. It is not independently verified.");
      }); }}>
        <h3>Work eligibility · your self-report</h3>
        <p>Current review: {job.eligibility_review?.status ?? "Not recorded"}. {job.eligibility_review?.reason}</p>
        <p>Review the posting and your actual work rights. Neither AI nor a free-text answer can approve legal eligibility. If uncertain, choose unknown and verify with the employer or a qualified adviser.</p>
        <label>Your eligibility status<select required value={eligibility} disabled={busy || dirty} onChange={event => { setEligibility(event.target.value as typeof eligibility); setConfirmed(false); }}><option value="">Choose explicitly</option><option value="eligible">I report that I am eligible</option><option value="ineligible">I report that I am not eligible</option><option value="unknown">Unknown — needs verification</option></select></label>
        <label>Reason for this self-report<textarea required maxLength={2000} value={reason} disabled={busy || dirty} onChange={event => { setReason(event.target.value); setConfirmed(false); }} /></label>
        <label className="workflow-check"><input type="checkbox" checked={confirmed} disabled={busy || dirty} onChange={event => setConfirmed(event.target.checked)} /><span>I explicitly confirm this job-specific self-report and understand it is not independently verified.</span></label>
        <button className="beta-secondary" disabled={busy || dirty || !eligibility || !reason.trim() || !confirmed}>Save eligibility self-report</button>
      </form>
      <div className="workflow-card"><h3>Role assessment</h3><p>{assessmentDisplay(job)}</p><FitExplanationBlock job={job} /><button onClick={() => setStep("documents")}>Choose resume and assess role</button></div>
    </div>}
    {step === "documents" && <div className="workflow-stack">
      <div className="workflow-card"><h3>Assess and prepare</h3><p>Source resume + confirmed facts + saved job description form the preparation context. Generated documents are drafts; no guarantee of screening success.</p>
        <label>Source resume<select value={resumeId} disabled={busy} onChange={event => { setResumeId(event.target.value); setPacketKey(""); setFreshSourceId(missingBytesConfirmed ? event.target.value || null : null); setAcknowledgedOperations([]); invalidateReview(); }}><option value="">Choose a resume</option>{workspace.resumes.map(resume => <option key={resume.id} value={resume.id}>{resume.label}</option>)}</select></label>
        <label>Preparation approach<select value={variant} disabled={busy} onChange={event => { setVariant(event.target.value); setPacketKey(""); invalidateReview(); }}><option value="role_aligned">Role-aligned</option><option value="career_change">Career change</option></select></label>
        {!workspace.profile?.career_text?.trim() && <p className="beta-notice">Confirm career facts below (Review extracted facts), or add them in your profile, before preparation.</p>}
        {!description.trim() && <p className="beta-notice">Add the full job description in Role &amp; eligibility.</p>}{dirty && <p className="beta-notice">Save your edited job description first.</p>}
        <label className="workflow-check"><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} /><span>Enable AI assistance for this role. When I click Assess or Prepare, the selected resume, career facts, saved answers and job description may be sent to the configured AI provider.</span></label>
        {!recoveryClear(recovery, scope) && <p className="beta-notice">{recovery.phase !== "checked" ? "Recovery records are not verified. AI remains blocked while checking or after a failed check. Use Check pending artifact operations below." : "An earlier artifact save is unresolved. Attempt recovery for every operation below; recovery never calls AI."}</p>}
        {missingBytesConfirmed && <fieldset className="workflow-fieldset" disabled={busy}><legend>Optional new paid preparation — not recovery</legend><p>Recovery confirmed missing bytes for every listed operation. Upload a fresh source below if needed, then select the source resume explicitly above. The default selection is not consent. Old journals and saved files remain unchanged.</p><p>This is a separate AI preparation that may spend your paid AI budget. It does not restore missing files, resolve or cancel old operations, or delete anything. Each acknowledgement applies to one Prepare click in this Studio session only. Assess remains blocked while recovery is unresolved.</p>
          {recovery.operations.map(operation => <label className="workflow-check" key={operation.id}><input type="checkbox" disabled={!freshSourceId || freshSourceId !== resumeId || !selectedResume} checked={acknowledgedOperations.includes(operation.id)} onChange={event => setAcknowledgedOperations(current => event.target.checked ? [...current.filter(id => id !== operation.id), operation.id] : current.filter(id => id !== operation.id))} /><span>I acknowledge missing file {operation.filename} (operation {operation.id}). With my explicitly selected source, I choose one new paid AI preparation while this old journal stays unresolved.</span></label>)}
        </fieldset>}
        <div className="workflow-actions"><button className="beta-secondary" disabled={busy || !canAnalyze} onClick={() => void analyze("rank")}>Assess role with AI</button><button className="beta-primary" disabled={busy || !canPrepare} onClick={() => void analyze("prepare")}>Prepare draft documents</button></div>
        {job.score != null && <><p>{assessmentDisplay(job)}</p><FitExplanationBlock job={job} /></>}
      </div>
      <DocumentReviewPanel userId={userId} jobId={job.id} refreshKey={artifacts.map(artifact => artifact.id).join(":")} />
      <div className="workflow-card"><h3>Saved document versions</h3><p>Historical files may reflect an older posting, resume or profile. Review against the current facts; preparing again does not remove previous versions.</p>
        {artifacts.length === 0 ? <p>No generated documents for this role yet.</p> : <ul className="workflow-files">{artifacts.map(artifact => <li key={artifact.id}><div><strong title={artifact.filename}>{documentLabel(artifact)}</strong><small>{artifact.created_at ? new Date(artifact.created_at).toLocaleString() : "Date unavailable"}{preparedIds.includes(artifact.id) ? " · Prepared in this session" : ""}</small></div><button className="beta-secondary" disabled={busy} onClick={() => void act(() => downloadMobileFile(userId, "artifacts", artifact.id, artifact.filename, request.current.signal))}>Download document</button></li>)}</ul>}
      </div>
      <RecoveryPanel userId={userId} kind="artifact" jobId={job.id} refreshKey={recoveryKey} onStatusChange={receiveRecovery} onRecovered={async () => { await load(); setAiHold(false); invalidateReview(); }} />
      <DocumentsPanel session={session} onChanged={async () => { invalidateReview(); await load(); }} />
    </div>}
    {step === "questions" && <div className="workflow-stack"><h3>Facts needing your input</h3><p>Only give answers you know to be accurate. Saving an answer does not automatically rerun AI or confirm work eligibility. Return to Documents to assess or prepare again.</p>
      {questions.length ? questions.map(question => <QuestionForm key={question.id + (question.answer ?? "")} question={question} disabled={busy} onAnswer={(answer, remember) => act(async () => {
        await mobileRequest(userId, `/questions/${question.id}/answer`, { method: "POST", signal: request.current.signal, body: { answer, remember } });
        invalidateReview(); await load(); setNotice("Answer saved. Rerun assessment or preparation explicitly when you are ready.");
      })} />) : <p>No saved questions were returned for this role or profile.</p>}
      <button onClick={() => setStep("documents")}>Return to document preparation</button>
    </div>}
    {step === "review" && <div className="workflow-stack">
      <div className="workflow-card"><h3>Select one document generation</h3>
        <p>Source: {selectedResume?.label ?? "Choose a source in Documents"}. Approach: {variant.replace(/_/g, " ")}. Resume and cover letter must belong to the same generation and format; historical generations are never mixed.</p>
        <label>Document packet<select value={packet?.key ?? ""} disabled={busy || !snapshotVerified || Boolean(readIssue)} onChange={event => { setPacketKey(event.target.value); setReview(null); }}><option value="">Choose a generation explicitly</option>{packets.map(item => <option key={item.key} value={item.key}>{new Date(item.generatedAt).toLocaleString()} · {item.pair ? item.pair[0].filename.endsWith(".pdf") ? "PDF pair" : "DOCX pair" : "Incomplete / ambiguous"} · {item.artifacts[0].id.slice(0, 8)}</option>)}</select></label>
        {!packets.length && <p>No server-verified generation is available for this source and approach. Historical files remain downloadable in Documents, but their filenames alone cannot establish a current matching packet. No AI work is required to record an external submission.</p>}
        {packet?.issue && <p className="beta-notice" role="status">{packet.issue}</p>}
        {packet?.pair && <ul className="workflow-files">{packet.pair.map(artifact => <li key={artifact.id}><span>{documentLabel(artifact)}</span><button className="beta-secondary" disabled={busy} onClick={() => void act(() => downloadMobileFile(userId, "artifacts", artifact.id, artifact.filename, request.current.signal))}>Download selected {artifact.kind === "tailored_resume" ? "resume" : "cover letter"}</button></li>)}</ul>}
        <p className="beta-notice">The server binds each packet to its generation, source resume, posting and career context. Changing facts, preferences, source files or answers invalidates its review. Ready saves your review across devices, not a guarantee of accuracy or an employer submission.</p>
        {readinessError && <p role="alert">Packet verification unavailable: {readinessError}</p>}
        {durableReviewed && <p role="status">Your review of this exact packet is saved and current. To clear Ready, save a draft record below.</p>}
        {serverReadiness?.review && !serverReadiness.review.current && <p role="status">A previous review is saved, but its packet or context is no longer current. Historical review does not make this application Ready.</p>}
        {!snapshotVerified && <p role="alert">The workspace read is not verified. Refresh saved Studio before reviewing or marking ready.</p>}
        {readIssue && <p role="alert">{readIssue}</p>}
      </div>
      <div className="workflow-card"><h3>Human review before external apply</h3><ul><li>Job-specific eligibility self-report: {eligible ? "Eligible (not independently verified)" : "Still needs review"}</li><li>Resume and cover letter for selected source: {hasDocuments ? "Saved versions available" : "Not both available"}</li><li>Unanswered returned questions: {pending.length}</li></ul>
        <label className="workflow-check"><input type="checkbox" checked={reviewed} disabled={busy || durableReviewed || !snapshotVerified || Boolean(readIssue) || !hasDocuments || dirty || Boolean(packet?.issue)} onChange={event => setReview(event.target.checked && packet ? packetReview(packet, contextKey) : null)} /><span>I downloaded and reviewed the selected same-generation resume and cover letter against the current job, source resume and career facts. I checked accuracy, dates, qualifications and all answers.</span></label>
        {packetIssue && <p role="status">{packetIssue}</p>}
        <p>Ready is a personal checklist state, not a submission or independent approval.</p><div className="workflow-actions"><button disabled={busy} onClick={() => void saveApplication("draft")}>Save draft record</button><button className="beta-primary" disabled={busy || !canReady} onClick={() => void saveApplication("ready")}>Mark ready for manual apply</button></div>
      </div>
      <form className="workflow-card" onSubmit={event => { event.preventDefault(); void saveApplication("submitted"); }}>
        <h3>Already applied outside this app?</h3><p>Open the original employer posting and complete its application yourself. This tracker cannot fill, send or verify that submission.</p>
        <label>External submission note<textarea required maxLength={8000} value={notes} disabled={busy} onChange={event => { setNotes(event.target.value); setSubmittedConfirmed(false); }} placeholder="Where and when you applied; optional confirmation reference. Do not paste credentials." /></label>
        <label className="workflow-check"><input type="checkbox" checked={submittedConfirmed} disabled={busy} onChange={event => setSubmittedConfirmed(event.target.checked)} /><span>I confirm I already submitted this application outside The Job Pursuit.</span></label>
        <button className="beta-secondary" disabled={busy || !notes.trim() || !submittedConfirmed}>Record external submission</button>
      </form>
    </div>}
  </section>;
}

function QuestionForm({ question, disabled, onAnswer }: { question: BetaQuestion; disabled: boolean; onAnswer: (answer: string, remember: boolean) => Promise<void> }) {
  const [answer, setAnswer] = useState(question.answer ?? "");
  const [remember, setRemember] = useState(question.remember ?? false);
  return <form className="workflow-card" aria-label={`Answer: ${question.prompt}`} onSubmit={event => { event.preventDefault(); if (answer.trim()) void onAnswer(answer.trim(), remember); }}>
    <p>{question.job_id ? "For this role" : "Profile question"} · {question.status}</p>
    <label>{question.prompt}<textarea required maxLength={8000} value={answer} disabled={disabled} onChange={event => setAnswer(event.target.value)} /></label>
    <label className="workflow-check"><input type="checkbox" checked={remember} disabled={disabled} onChange={event => setRemember(event.target.checked)} /><span>Remember this answer within its scope for future assistance. The answer remains on this question even when unchecked.</span></label>
    <button disabled={disabled || !answer.trim()}>Save answer</button>
  </form>;
}
