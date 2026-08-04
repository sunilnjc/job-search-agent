import type { Job } from "../types";
import { useUnexcludeJob } from "../hooks/useJobs";

interface Props {
  jobs: Job[];
  onOpenJob: (id: number) => void;
}

export function MobileExcluded({ jobs, onOpenJob }: Props) {
  const unexcludeJob = useUnexcludeJob();
  const excluded = jobs.filter((job) => job.excluded_reason);

  if (excluded.length === 0) {
    return (
      <div className="mobile-empty-state">
        <div className="mobile-empty-icon">⊘</div>
        <h2>No excluded jobs</h2>
        <p>Roles you decide are not a fit will stay here, with the reason, until you restore them.</p>
      </div>
    );
  }

  return (
    <section className="mobile-applications" aria-label="Excluded jobs">
      <div className="mobile-section-heading">
        <div>
          <p className="mobile-eyebrow">ARCHIVE</p>
          <h2>Excluded jobs</h2>
        </div>
        <span className="mobile-total">{excluded.length}</span>
      </div>
      <div className="mobile-application-list">
        {excluded.map((job) => (
          <article key={job.id} className="mobile-excluded-card">
            <button className="mobile-application-main" onClick={() => onOpenJob(job.id)}>
              <span className="mobile-application-copy">
                <strong>{job.title}</strong>
                <span>{job.company} · {job.location}</span>
              </span>
            </button>
            <p>{job.excluded_reason}</p>
            <button className="mobile-restore" onClick={() => unexcludeJob.mutate(job.id)}>
              Restore to queue
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}
