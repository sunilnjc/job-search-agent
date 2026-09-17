import type { BetaJob } from "./types";
import { safePostingUrl } from "./workspace";
import { WorkspaceDialog } from "./WorkspaceDialog";
import { assessmentDisplay } from "./assessmentDisplay";
import { FitExplanationBlock } from "./FitExplanationBlock";

export type JobDetailWorkspaceProps = {
  job: BetaJob;
  onClose: () => void;
  onOpenStudio: (job: BetaJob) => void;
};

function labelForEligibility(status: BetaJob["eligibility_status"]) {
  const labels: Record<BetaJob["eligibility_status"], string> = {
    eligible: "Eligibility recorded — review in Studio",
    ineligible: "Not currently eligible",
    needs_review: "Eligibility needs review",
    unknown: "Eligibility not checked yet",
  };
  return labels[status];
}

function labelForWorkplace(workplace: BetaJob["workplace_type"]) {
  if (!workplace || workplace === "unknown") return "Workplace not listed";
  return workplace.charAt(0).toUpperCase() + workplace.slice(1);
}

function formatDate(date: string | null) {
  if (!date) return null;
  const parsed = new Date(date);
  if (Number.isNaN(parsed.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(parsed);
}

/**
 * Presentational detail sheet for a beta job. It intentionally renders only
 * persisted job facts; AI evidence, match explanations, and live freshness
 * checks are represented as future states rather than invented content.
 */
export function JobDetailWorkspace({ job, onClose, onOpenStudio }: JobDetailWorkspaceProps) {
  const validatedDate = formatDate(job.last_validated_at);
  const hasLiveValidation = Boolean(validatedDate);
  const postingUrl = safePostingUrl(job.source_url);

  return (
    <WorkspaceDialog className="beta-job-detail-backdrop" labelledBy={`job-detail-title-${job.id}`} onClose={onClose}>
      <section
        className="beta-job-detail-sheet"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="beta-job-detail-header">
          <div className="beta-job-detail-heading">
            <p className="beta-job-detail-eyebrow">Role workspace</p>
            <h2 id={`job-detail-title-${job.id}`}>{job.title}</h2>
            <p className="beta-job-detail-company">{job.company_name}</p>
            <p className="beta-job-detail-location">
              {job.location_text || "Location not listed"} · {labelForWorkplace(job.workplace_type)}
            </p>
          </div>
          <button className="beta-job-detail-close" type="button" onClick={onClose} aria-label="Close job details">
            ×
          </button>
        </header>

        <div className="beta-job-detail-badges" aria-label="Role status">
          <span className="beta-job-detail-badge beta-job-detail-badge-source">Source: {job.source === "manual" ? "Saved posting — see original source" : job.source}</span>
          <span className={`beta-job-detail-badge beta-job-detail-badge-eligibility ${job.eligibility_status}`}>
            {labelForEligibility(job.eligibility_status)}
          </span>
          <span className={`beta-job-detail-badge beta-job-detail-badge-freshness ${hasLiveValidation ? "validated" : "pending"}`}>
            {hasLiveValidation ? `Last recorded validation ${validatedDate}` : "Posting freshness not verified"}
          </span>
        </div>

        <div className="beta-job-detail-content">
          <section className="beta-job-detail-section" aria-labelledby={`job-detail-overview-${job.id}`}>
            <div className="beta-job-detail-section-heading">
              <p className="beta-job-detail-kicker">01</p>
              <h3 id={`job-detail-overview-${job.id}`}>Overview</h3>
            </div>
            <dl className="beta-job-detail-facts">
              <div><dt>Company</dt><dd>{job.company_name}</dd></div>
              <div><dt>Location</dt><dd>{job.location_text || "Not listed"}</dd></div>
              <div><dt>Workplace</dt><dd>{labelForWorkplace(job.workplace_type)}</dd></div>
              <div><dt>Board status</dt><dd>{job.status.replace("_", " ")}</dd></div>
            </dl>
            {postingUrl && <a className="beta-job-detail-source-link" href={postingUrl} target="_blank" rel="noreferrer">
              View original posting ↗
            </a>}
          </section>

          <section className="beta-job-detail-section" aria-labelledby={`job-detail-match-${job.id}`}>
            <div className="beta-job-detail-section-heading">
              <p className="beta-job-detail-kicker">02</p>
              <h3 id={`job-detail-match-${job.id}`}>Match &amp; eligibility</h3>
            </div>
            <p className="beta-job-detail-status-copy">{labelForEligibility(job.eligibility_status)}.</p>
            <p className="beta-job-detail-muted">
              {job.score != null ? assessmentDisplay(job) : "Open Studio to assess this role against your selected resume and confirmed career facts."} This is not an ATS score, hiring probability or independent verification of work rights.
            </p>
            {job.score != null && <FitExplanationBlock job={job} />}
          </section>

          <section className="beta-job-detail-section" aria-labelledby={`job-detail-preparation-${job.id}`}>
            <div className="beta-job-detail-section-heading">
              <p className="beta-job-detail-kicker">03</p>
              <h3 id={`job-detail-preparation-${job.id}`}>Preparation</h3>
            </div>
            <p className="beta-job-detail-muted">
              Open Studio to fetch the full saved description, edit requirements, explicitly review eligibility, answer questions and prepare draft documents. You review and apply on the employer’s site yourself.
            </p>
            <button className="beta-job-detail-primary-action" type="button" onClick={() => onOpenStudio(job)}>
              Open Application Studio
            </button>
          </section>
        </div>

        <footer className="beta-job-detail-footer">
          <button className="beta-job-detail-secondary-action" type="button" onClick={onClose}>Back to roles</button>
          <button className="beta-job-detail-primary-action" type="button" onClick={() => onOpenStudio(job)}>
            Prepare application
          </button>
        </footer>
      </section>
    </WorkspaceDialog>
  );
}
