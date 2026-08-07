import { useEffect, useState } from "react";
import { useExcludeJob, useGenerateDraft, useGenerateGaps, useJob, useUpdateStatus } from "../hooks/useJobs";
import { api, coverLetterPdfUrl, resumeDocxUrl, resumePdfUrl, shareOrDownloadDocument } from "../api/client";
import { ApplicationChat } from "./ApplicationChat";
import type { OutreachDraft, Status } from "../types";

type Tab = "description" | "gaps" | "cover_letter" | "resume_tailoring" | "chat" | "outreach";

const TAB_LABELS: Record<Tab, string> = {
  description: "Description",
  gaps: "Gap Analysis",
  cover_letter: "Cover Letter",
  resume_tailoring: "Resume Tailoring",
  chat: "Ask AI",
  outreach: "Outreach",
};

// Same-origin downloads honour this attribute, which is the reliable way to get a
// company-specific filename on iOS — Safari otherwise names the file after the URL path.
function docName(company: string, kind: string, extension: string): string {
  const safe = company.replace(/[/\\:*?"<>|]/g, "-").trim();
  return `Sunilkumar Kalabandi - ${safe} - ${kind}.${extension}`;
}

interface Props {
  jobId: number;
  onClose: () => void;
}

export function JobDetailModal({ jobId, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("description");
  const [documentError, setDocumentError] = useState<string | null>(null);
  const [displayStatus, setDisplayStatus] = useState<Status | null>(null);
  const [outreach, setOutreach] = useState<OutreachDraft | null>(null);
  const [outreachRecipient, setOutreachRecipient] = useState("");
  const [outreachError, setOutreachError] = useState<string | null>(null);
  const [outreachBusy, setOutreachBusy] = useState(false);
  const [linkCheck, setLinkCheck] = useState<string | null>(null);
  const [checkingLink, setCheckingLink] = useState(false);
  const { data: job, isLoading } = useJob(jobId);
  const generateDraft = useGenerateDraft();
  const generateGaps = useGenerateGaps();
  const updateStatus = useUpdateStatus();
  const excludeJob = useExcludeJob();

  useEffect(() => {
    setDisplayStatus(null);
    setOutreach(null);
    setOutreachRecipient("");
    setOutreachError(null);
    setLinkCheck(null);
  }, [jobId]);

  const createOutreach = async () => {
    setOutreachBusy(true); setOutreachError(null);
    try {
      const result = await api.createOutreach(jobId);
      setOutreach(result); setOutreachRecipient(result.recipient ?? "");
    } catch (error) {
      setOutreachError(error instanceof Error ? error.message : "Could not create draft.");
    } finally { setOutreachBusy(false); }
  };

  const sendOutreach = async () => {
    if (!window.confirm(`Send this reviewed email to ${outreachRecipient}?`)) return;
    setOutreachBusy(true); setOutreachError(null);
    try { setOutreach(await api.sendOutreach(jobId, outreachRecipient)); }
    catch (error) { setOutreachError(error instanceof Error ? error.message : "Could not send email."); }
    finally { setOutreachBusy(false); }
  };

  const validateLink = async () => {
    setCheckingLink(true); setLinkCheck(null);
    try {
      const result = await api.validateJob(jobId);
      setLinkCheck(result.available === true
        ? "✓ Live employer link confirmed"
        : result.available === false
          ? `Unavailable: ${result.reason ?? "role was removed"}`
          : `Manual check needed: ${result.reason ?? "the job site blocked automated verification"}`);
    } catch (error) { setLinkCheck(error instanceof Error ? error.message : "Could not verify link."); }
    finally { setCheckingLink(false); }
  };

  if (isLoading || !job) {
    return (
      <div className="modal-overlay" onClick={onClose}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>Loading…</div>
      </div>
    );
  }

  // Update the action immediately after a successful mutation. Waiting for the periodic
  // board refresh left the stale "Mark applied" button tappable on iPhone.
  const currentStatus = displayStatus ?? job.status;

  const content: Record<Exclude<Tab, "chat" | "outreach">, string | null> = {
    description: job.description,
    gaps: job.gap_analysis,
    cover_letter: job.cover_letter,
    resume_tailoring: job.resume_tailoring,
  };

  const isGenerating = generateDraft.isPending || generateGaps.isPending;
  const applicationAction = currentStatus === "applied" ? "Move to interview" : "Mark applied";

  const markApplicationProgress = () => {
    const nextStatus: Status = currentStatus === "applied" ? "interviewing" : "applied";
    updateStatus.mutate(
      { id: job.id, status: nextStatus },
      { onSuccess: () => setDisplayStatus(nextStatus) },
    );
  };

  const exclude = () => {
    const reason = window.prompt("Why exclude this job?", "Not a fit");
    if (reason?.trim()) {
      excludeJob.mutate({ id: job.id, reason: reason.trim() });
      onClose();
    }
  };

  const saveDocument = (url: string, filename: string) => {
    setDocumentError(null);
    void shareOrDownloadDocument(url, filename).catch((error: unknown) => {
      // Closing the native share sheet is not an error the user needs to see.
      if (error instanceof DOMException && error.name === "AbortError") return;
      setDocumentError("Could not open the download. Please try again.");
    });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h2>{job.title}</h2>
            <div className="modal-subtitle">
              {job.company} · {job.location}
              {job.llm_score !== null && ` · score ${job.llm_score}/10`}
            </div>
            <a href={job.url} target="_blank" rel="noreferrer" className="modal-link">
              View original posting →
            </a>
            <div className="modal-source">Source: {job.source.replace(":", " · ")}</div>
          </div>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-tabs">
          {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
            <button
              key={t}
              className={tab === t ? "modal-tab active" : "modal-tab"}
              onClick={() => setTab(t)}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>

        <div className="modal-body">
          {tab === "chat" ? (
            <ApplicationChat jobId={job.id} company={job.company} />
          ) : tab === "outreach" ? (
            <div className="outreach-panel">
              <p className="modal-empty">Creates a truthful, role-specific email. It only detects public careers/recruiting inboxes; it never guesses a recruiter's private address.</p>
              {!outreach ? (
                <button disabled={outreachBusy} onClick={() => void createOutreach()}>
                  {outreachBusy ? "Creating draft…" : "Create outreach draft"}
                </button>
              ) : (
                <>
                  <label>Recipient (review or enter a public recruiting inbox)
                    <input value={outreachRecipient} onChange={(event) => setOutreachRecipient(event.target.value)} placeholder="careers@company.com" type="email" />
                  </label>
                  <label>Subject<input value={outreach.subject} readOnly /></label>
                  <label>Draft<textarea value={outreach.body} readOnly rows={10} /></label>
                  <p className="outreach-attachments">Attachments on send: tailored resume PDF and cover letter PDF.</p>
                  {outreach.status === "sent" ? <p className="outreach-sent">✓ Sent</p> : <button disabled={outreachBusy || !outreachRecipient} onClick={() => void sendOutreach()}>{outreachBusy ? "Sending…" : "Send reviewed email"}</button>}
                </>
              )}
              {outreachError && <p className="modal-document-error" role="alert">{outreachError}</p>}
            </div>
          ) : content[tab] ? (
            <pre className="modal-text">{content[tab]}</pre>
          ) : tab === "description" ? (
            <p className="modal-empty">No description available.</p>
          ) : (
            <div className="modal-empty">
              <p>Not generated yet.</p>
              {tab === "gaps" && (
                <button disabled={isGenerating} onClick={() => generateGaps.mutate(job.id)}>
                  {generateGaps.isPending ? "Analyzing…" : "Generate gap analysis"}
                </button>
              )}
              {(tab === "cover_letter" || tab === "resume_tailoring") && (
                <button disabled={isGenerating} onClick={() => generateDraft.mutate(job.id)}>
                  {generateDraft.isPending ? "Drafting…" : "Generate cover letter + resume tailoring"}
                </button>
              )}
            </div>
          )}
          {tab === "resume_tailoring" && content[tab] && job.has_resume_pdf && (
            <button className="modal-download modal-download-primary" onClick={() => saveDocument(resumePdfUrl(job.id), docName(job.company, "Resume", "pdf"))}>
              ⬇ Save/share resume (.pdf)
            </button>
          )}
          {tab === "resume_tailoring" && content[tab] && job.has_resume_docx && (
            <button className="modal-download" onClick={() => saveDocument(resumeDocxUrl(job.id), docName(job.company, "Resume", "docx"))}>
              ⬇ Save/share resume (.docx)
            </button>
          )}
          {tab === "cover_letter" && content[tab] && job.has_cover_letter_pdf && (
            <button className="modal-download modal-download-primary" onClick={() => saveDocument(coverLetterPdfUrl(job.id), docName(job.company, "Cover Letter", "pdf"))}>
              ⬇ Save/share cover letter (.pdf)
            </button>
          )}
          {documentError && <p className="modal-document-error" role="alert">{documentError}</p>}
          {(tab === "gaps" || tab === "cover_letter" || tab === "resume_tailoring") && content[tab] && (
            <button
              className="modal-regenerate"
              disabled={isGenerating}
              onClick={() =>
                tab === "gaps" ? generateGaps.mutate(job.id) : generateDraft.mutate(job.id)
              }
            >
              Regenerate
            </button>
          )}
        </div>
        <footer className="modal-action-footer">
          <button className="modal-verify-link" onClick={() => void validateLink()} disabled={checkingLink}>
            {checkingLink ? "Checking…" : "Verify live link"}
          </button>
          <a href={job.url} target="_blank" rel="noreferrer" className="modal-open-posting">
            Open job site ↗
          </a>
          {currentStatus !== "interviewing" && currentStatus !== "rejected" && currentStatus !== "offer" && (
            <button className="modal-mark-applied" onClick={markApplicationProgress} disabled={updateStatus.isPending}>
              {updateStatus.isPending ? "Saving…" : applicationAction}
            </button>
          )}
          <button className="modal-not-fit" onClick={exclude} disabled={excludeJob.isPending}>Not a fit</button>
        </footer>
        {linkCheck && <p className="modal-link-check" role="status">{linkCheck}</p>}
      </div>
    </div>
  );
}
