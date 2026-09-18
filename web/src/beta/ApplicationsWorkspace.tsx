import { useMemo, useState } from "react";
import type { BetaApplication, BetaJob } from "./types";
import { applicationCounts } from "./workspace";

export type ApplicationsWorkspaceProps = {
  jobs: BetaJob[];
  applications: BetaApplication[];
  onOpenDetail: (job: BetaJob) => void;
  onOpenStudio: (job: BetaJob) => void;
  onFindRoles?: () => void;
};

type StatusFilter = "all" | "draft" | "submitted" | "closed";

const statusLabel: Record<BetaApplication["status"], string> = {
  draft: "Preparing", ready: "Ready", submitted: "Submitted", interviewing: "Interviewing",
  rejected: "Not selected", withdrawn: "Withdrawn", closed: "Closed",
};

const eligibilityLabel: Record<BetaJob["eligibility_status"], string> = {
  eligible: "Work rights recorded",
  ineligible: "Not currently eligible",
  needs_review: "Needs work-rights review",
  unknown: "Work rights not recorded",
};

function workplaceLabel(workplace: BetaJob["workplace_type"]) {
  if (!workplace || workplace === "unknown") return "Workplace not listed";
  return workplace.charAt(0).toUpperCase() + workplace.slice(1);
}

/**
 * Application progress comes from applications.status, never a saved job label.
 * Retain history even when a job falls outside the workspace's loaded page.
 */
export function ApplicationsWorkspace({ jobs, applications, onOpenDetail, onOpenStudio, onFindRoles }: ApplicationsWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const counts = applicationCounts(applications);
  const jobsById = useMemo(() => new Map(jobs.map((job) => [job.id, job])), [jobs]);
  const visibleApplications = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return applications.filter((application) => {
      const job = jobsById.get(application.job_id);
      const matchesStatus = statusFilter === "all"
        || (statusFilter === "draft" && ["draft", "ready"].includes(application.status))
        || (statusFilter === "closed" && ["rejected", "withdrawn", "closed"].includes(application.status))
        || application.status === statusFilter;
      const searchable = [job?.title, job?.company_name, job?.location_text, statusLabel[application.status]].filter(Boolean).join(" ").toLocaleLowerCase();
      return matchesStatus && (!normalizedQuery || searchable.includes(normalizedQuery));
    });
  }, [applications, jobsById, query, statusFilter]);

  return (
    <section className="beta-applications-workspace" aria-labelledby="applications-workspace-title">
      <style>{applicationsWorkspaceStyles}</style>
      <header className="beta-applications-workspace-header">
        <div>
          <p className="beta-applications-workspace-eyebrow">REVIEW</p>
          <h2 id="applications-workspace-title">Packets ready for you to apply.</h2>
          <p>Review prepared drafts and your saved status notes. You open the employer site and submit yourself — this product never auto-applies.</p>
        </div>
      </header>

      <div className="beta-applications-workspace-summary" aria-label="Application summary">
        <article><strong>{counts.draft}</strong><span>Preparing</span></article>
        <article><strong>{counts.submitted}</strong><span>You marked submitted</span></article>
        <article><strong>{counts.active}</strong><span>Active records</span></article>
      </div>

      <div className="beta-applications-workspace-controls">
        <label className="beta-applications-workspace-search">
          <span className="beta-applications-workspace-sr-only">Search applications</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search roles, companies, locations" type="search" />
        </label>
        <div className="beta-applications-workspace-filters" role="group" aria-label="Filter by application status">
          {(["all", "draft", "submitted", "closed"] as StatusFilter[]).map((filter) => (
            <button key={filter} type="button" aria-pressed={statusFilter === filter} className={statusFilter === filter ? "is-active" : ""} onClick={() => setStatusFilter(filter)}>
              {filter === "all" ? `All ${applications.length}` : filter === "closed" ? "Closed" : statusLabel[filter]}
            </button>
          ))}
        </div>
      </div>

      {visibleApplications.length === 0 ? (
        <div className="beta-applications-workspace-empty">
          <h3>{applications.length === 0 ? "Your next chapter starts with one role." : "No applications match these filters"}</h3>
          <p>{applications.length === 0 ? "Save a role in Discover, record work rights in Rank, prepare drafts in Prepare, then save a draft record in Final check. Nothing is submitted automatically." : "Try another search phrase or reset the status filter."}</p>
          {applications.length === 0 && onFindRoles && <button type="button" className="beta-applications-workspace-primary" onClick={onFindRoles}>Find roles in Discover</button>}
          {applications.length > 0 && <button type="button" onClick={() => { setQuery(""); setStatusFilter("all"); }}>Reset filters</button>}
        </div>
      ) : (
        <div className="beta-applications-workspace-list">
          {visibleApplications.map((application) => {
            const job = jobsById.get(application.job_id);
            return <article className="beta-applications-workspace-row" key={application.id}>
              <div className="beta-applications-workspace-role">
                <div className="beta-applications-workspace-badges">
                  <span className={`beta-applications-workspace-status ${application.status}`}>{statusLabel[application.status]}</span>
                  {job && <span className={`beta-applications-workspace-eligibility ${job.eligibility_status}`}>{eligibilityLabel[job.eligibility_status]}</span>}
                </div>
                <h3>{job?.title || "Role details not loaded"}</h3>
                <p>{job?.company_name || "Application history retained"}</p>
                <small>{job ? `${job.location_text || "Location not listed"} · ${workplaceLabel(job.workplace_type)}` : "This role may be outside your 200 most recent saved jobs."}</small>
              </div>
              <div className="beta-applications-workspace-actions">
                <button type="button" disabled={!job} className="beta-applications-workspace-secondary" onClick={() => job && onOpenDetail(job)}>View role</button>
                <button type="button" disabled={!job} className="beta-applications-workspace-primary" onClick={() => job && onOpenStudio(job)}>Application Studio</button>
              </div>
            </article>;
          })}
        </div>
      )}
    </section>
  );
}

const applicationsWorkspaceStyles = `
  .beta-applications-workspace { display: grid; gap: 1.25rem; color: var(--beta-text, #f6f0ff); }
  .beta-applications-workspace-header { display: flex; align-items: end; justify-content: space-between; gap: 1rem; }
  .beta-applications-workspace-eyebrow { margin: 0 0 .45rem; color: var(--beta-accent, #c78cff); font-size: .7rem; font-weight: 800; letter-spacing: .12em; }
  .beta-applications-workspace h2, .beta-applications-workspace h3, .beta-applications-workspace p { margin-top: 0; }
  .beta-applications-workspace h2 { margin-bottom: .45rem; font-size: clamp(1.65rem, 6vw, 2.4rem); letter-spacing: -.05em; }
  .beta-applications-workspace-header p:not(.beta-applications-workspace-eyebrow) { margin-bottom: 0; color: var(--beta-muted, #a69eb4); max-width: 39rem; }
  .beta-applications-workspace-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: .65rem; }
  .beta-applications-workspace-summary article { min-width: 0; padding: 1rem; border: 1px solid var(--beta-line, rgba(255,255,255,.12)); border-radius: 1rem; background: var(--beta-surface, rgba(255,255,255,.045)); }
  .beta-applications-workspace-summary strong { display: block; font-size: 1.55rem; line-height: 1; letter-spacing: -.06em; }
  .beta-applications-workspace-summary span { display: block; margin-top: .45rem; color: var(--beta-muted, #a69eb4); font-size: .75rem; line-height: 1.2; }
  .beta-applications-workspace-controls { display: grid; gap: .75rem; }
  .beta-applications-workspace-search input { width: 100%; box-sizing: border-box; padding: .88rem 1rem; border: 1px solid var(--beta-line, rgba(255,255,255,.16)); border-radius: .85rem; background: var(--beta-surface, rgba(255,255,255,.04)); color: inherit; font: inherit; }
  .beta-applications-workspace-filters { display: flex; gap: .5rem; overflow-x: auto; padding-bottom: .15rem; }
  .beta-applications-workspace-filters button { white-space: nowrap; padding: .58rem .8rem; border: 1px solid var(--beta-line, rgba(255,255,255,.16)); border-radius: 999px; background: transparent; color: var(--beta-muted, #a69eb4); font: inherit; font-size: .78rem; cursor: pointer; }
  .beta-applications-workspace-filters button.is-active { border-color: transparent; background: var(--beta-accent, #b77aff); color: #1c1025; font-weight: 800; }
  .beta-applications-workspace-list { display: grid; gap: .75rem; }
  .beta-applications-workspace-row { display: grid; gap: 1rem; padding: 1rem; border: 1px solid var(--beta-line, rgba(255,255,255,.12)); border-radius: 1rem; background: var(--beta-surface, rgba(255,255,255,.04)); }
  .beta-applications-workspace-badges { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .75rem; }
  .beta-applications-workspace-status, .beta-applications-workspace-eligibility { padding: .3rem .5rem; border-radius: .4rem; font-size: .66rem; font-weight: 800; letter-spacing: .045em; text-transform: uppercase; }
  .beta-applications-workspace-status.ready { background: #e5c5ff; color: #2d1340; }
  .beta-applications-workspace-status.applied { background: #b6eed1; color: #083d25; }
  .beta-applications-workspace-eligibility.eligible { background: rgba(78, 205, 132, .18); color: #8ff0bd; }
  .beta-applications-workspace-eligibility.needs_review { background: rgba(250, 190, 80, .18); color: #ffd37e; }
  .beta-applications-workspace-eligibility.ineligible { background: rgba(255, 105, 128, .17); color: #ffabb9; }
  .beta-applications-workspace-eligibility.unknown { background: rgba(174, 163, 195, .16); color: #d2c7df; }
  .beta-applications-workspace-role h3 { margin-bottom: .25rem; font-size: 1.05rem; line-height: 1.25; }
  .beta-applications-workspace-role p { margin-bottom: .3rem; color: var(--beta-text, #f6f0ff); font-weight: 650; }
  .beta-applications-workspace-role small { color: var(--beta-muted, #a69eb4); }
  .beta-applications-workspace-actions { display: grid; grid-template-columns: 1fr 1fr; gap: .55rem; }
  .beta-applications-workspace-actions button, .beta-applications-workspace-empty button { min-height: 2.65rem; border-radius: .7rem; padding: .55rem .7rem; font: inherit; font-weight: 750; cursor: pointer; }
  .beta-applications-workspace-primary { border: 0; background: var(--beta-accent, #b77aff); color: #1c1025; }
  .beta-applications-workspace-secondary, .beta-applications-workspace-empty button { border: 1px solid var(--beta-line, rgba(255,255,255,.18)); background: transparent; color: inherit; }
  .beta-applications-workspace-empty { padding: 2.5rem 1rem; border: 1px dashed var(--beta-line, rgba(255,255,255,.18)); border-radius: 1rem; text-align: center; }
  .beta-applications-workspace-empty h3 { margin-bottom: .45rem; }
  .beta-applications-workspace-empty p { max-width: 30rem; margin: 0 auto 1rem; color: var(--beta-muted, #a69eb4); }
  .beta-applications-workspace-sr-only { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; }
  @media (min-width: 700px) {
    .beta-applications-workspace { gap: 1.5rem; }
    .beta-applications-workspace-controls { grid-template-columns: minmax(0, 1fr) auto; align-items: center; }
    .beta-applications-workspace-row { grid-template-columns: minmax(0, 1fr) auto; align-items: center; padding: 1.1rem 1.2rem; }
    .beta-applications-workspace-actions { display: flex; min-width: 14.5rem; }
    .beta-applications-workspace-actions button { flex: 1; }
  }
`;
