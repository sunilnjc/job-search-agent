import type { Job, Status } from "../types";
import { useUpdateStatus } from "../hooks/useJobs";

const APPLICATION_STATUSES: Status[] = ["applied", "interviewing", "rejected", "offer"];

interface Props {
  jobs: Job[];
  onOpenJob: (id: number) => void;
}

export function MobileApplications({ jobs, onOpenJob }: Props) {
  const updateStatus = useUpdateStatus();
  const applications = jobs
    .filter((job) => !job.excluded_reason && APPLICATION_STATUSES.includes(job.status))
    .sort((a, b) => APPLICATION_STATUSES.indexOf(a.status) - APPLICATION_STATUSES.indexOf(b.status));

  if (applications.length === 0) {
    return (
      <div className="mobile-empty-state">
        <div className="mobile-empty-icon">✓</div>
        <h2>No applications yet</h2>
        <p>Review a ready job, apply on the company site, then mark it as applied here.</p>
      </div>
    );
  }

  return (
    <section className="mobile-applications" aria-label="Applications">
      <div className="mobile-section-heading">
        <div>
          <p className="mobile-eyebrow">TRACKING</p>
          <h2>Applications</h2>
        </div>
        <span className="mobile-total">{applications.length}</span>
      </div>
      <div className="mobile-application-list">
        {applications.map((job) => (
          <article key={job.id} className="mobile-application-card">
            <button className="mobile-application-main" onClick={() => onOpenJob(job.id)}>
              <span className={`mobile-status-dot status-${job.status}`} aria-hidden="true" />
              <span className="mobile-application-copy">
                <strong>{job.title}</strong>
                <span>{job.company} · {job.location}</span>
              </span>
              {job.llm_score !== null && <span className="score-badge">{job.llm_score}/10</span>}
            </button>
            <label className="mobile-status-select">
              <span>Status</span>
              <select
                value={job.status}
                onChange={(event) => updateStatus.mutate({ id: job.id, status: event.target.value as Status })}
              >
                {APPLICATION_STATUSES.map((status) => (
                  <option key={status} value={status}>{status}</option>
                ))}
              </select>
            </label>
          </article>
        ))}
      </div>
    </section>
  );
}
