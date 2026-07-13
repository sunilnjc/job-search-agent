import { useState } from "react";
import { useJob, useGenerateDraft, useGenerateGaps } from "../hooks/useJobs";
import { coverLetterPdfUrl, resumeDocxUrl, resumePdfUrl } from "../api/client";
import { ApplicationChat } from "./ApplicationChat";

type Tab = "description" | "gaps" | "cover_letter" | "resume_tailoring" | "chat";

const TAB_LABELS: Record<Tab, string> = {
  description: "Description",
  gaps: "Gap Analysis",
  cover_letter: "Cover Letter",
  resume_tailoring: "Resume Tailoring",
  chat: "Ask AI",
};

interface Props {
  jobId: number;
  onClose: () => void;
}

export function JobDetailModal({ jobId, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("description");
  const { data: job, isLoading } = useJob(jobId);
  const generateDraft = useGenerateDraft();
  const generateGaps = useGenerateGaps();

  if (isLoading || !job) {
    return (
      <div className="modal-overlay" onClick={onClose}>
        <div className="modal" onClick={(e) => e.stopPropagation()}>Loading…</div>
      </div>
    );
  }

  const content: Record<Exclude<Tab, "chat">, string | null> = {
    description: job.description,
    gaps: job.gap_analysis,
    cover_letter: job.cover_letter,
    resume_tailoring: job.resume_tailoring,
  };

  const isGenerating = generateDraft.isPending || generateGaps.isPending;

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
            <a className="modal-download modal-download-primary" href={resumePdfUrl(job.id)} download>
              ⬇ Download resume (.pdf)
            </a>
          )}
          {tab === "resume_tailoring" && content[tab] && job.has_resume_docx && (
            <a className="modal-download" href={resumeDocxUrl(job.id)} download>
              ⬇ Download resume (.docx)
            </a>
          )}
          {tab === "cover_letter" && content[tab] && job.has_cover_letter_pdf && (
            <a className="modal-download modal-download-primary" href={coverLetterPdfUrl(job.id)} download>
              ⬇ Download cover letter (.pdf)
            </a>
          )}
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
      </div>
    </div>
  );
}
