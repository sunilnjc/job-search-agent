import type { BetaJob } from "./types";

export type JobDetailWorkspaceProps = {
  job: BetaJob;
  onClose: () => void;
  onOpenStudio: (job: BetaJob) => void;
};

function labelForEligibility(status: BetaJob["eligibility_status"]) {
  const labels: Record<BetaJob["eligibility_status"], string> = {
    eligible: "Eligibility confirmed",
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

  return (
    <div className="beta-job-detail-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="beta-job-detail-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`job-detail-title-${job.id}`}
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
          <span className="beta-job-detail-badge beta-job-detail-badge-source">Direct source: {job.source}</span>
          <span className={`beta-job-detail-badge beta-job-detail-badge-eligibility ${job.eligibility_status}`}>
            {labelForEligibility(job.eligibility_status)}
          </span>
          <span className={`beta-job-detail-badge beta-job-detail-badge-freshness ${hasLiveValidation ? "validated" : "pending"}`}>
            {hasLiveValidation ? `Validated ${validatedDate}` : "Freshness check pending"}
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
            <a className="beta-job-detail-source-link" href={job.source_url} target="_blank" rel="noreferrer">
              View original posting ↗
            </a>
          </section>

          <section className="beta-job-detail-section" aria-labelledby={`job-detail-match-${job.id}`}>
            <div className="beta-job-detail-section-heading">
              <p className="beta-job-detail-kicker">02</p>
              <h3 id={`job-detail-match-${job.id}`}>Match &amp; eligibility</h3>
            </div>
            <p className="beta-job-detail-status-copy">{labelForEligibility(job.eligibility_status)}.</p>
            <p className="beta-job-detail-muted">
              Match evidence, sponsorship signals, and role-specific eligibility reasoning will be populated after the job is validated against the source and your approved profile. This workspace will not guess fit facts.
            </p>
          </section>

          <section className="beta-job-detail-section" aria-labelledby={`job-detail-preparation-${job.id}`}>
            <div className="beta-job-detail-section-heading">
              <p className="beta-job-detail-kicker">03</p>
              <h3 id={`job-detail-preparation-${job.id}`}>Preparation</h3>
            </div>
            <p className="beta-job-detail-muted">
              Application Studio keeps tailored documents, employer questions, and final review in one private place. AI-created material will be grounded only in your approved career record.
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
    </div>
  );
}
