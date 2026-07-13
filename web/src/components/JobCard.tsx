import type { Job, Status } from "../types";
import { STATUSES } from "../types";
import { useExcludeJob, useUpdateStatus } from "../hooks/useJobs";

const ELIGIBILITY_LABEL: Record<string, string> = {
  worldwide: "REMOTE ANYWHERE",
  sponsors: "SPONSORS VISA",
  restricted: "RESTRICTED",
  "title-filtered": "FILTERED",
  unknown: "UNKNOWN",
};

const SUGGESTED_REASON: Record<string, string> = {
  restricted: "No work authorization for this location",
};

interface Props {
  job: Job;
  onOpen: (id: number) => void;
}

export function JobCard({ job, onOpen }: Props) {
  const updateStatus = useUpdateStatus();
  const excludeJob = useExcludeJob();
  const currentIndex = STATUSES.indexOf(job.status);
  const nextStatus = STATUSES[currentIndex + 1];
  const prevStatus = currentIndex > 0 ? STATUSES[currentIndex - 1] : undefined;

  const handleExclude = () => {
    const suggested = SUGGESTED_REASON[job.eligibility] ?? "";
    const reason = window.prompt("Why exclude this job? (e.g. \"not available in your region\")", suggested);
    if (reason && reason.trim()) {
      excludeJob.mutate({ id: job.id, reason: reason.trim() });
    }
  };

  return (
    <div className="job-card" onClick={() => onOpen(job.id)}>
      <div className="job-card-header">
        {job.llm_score !== null && <span className="score-badge">{job.llm_score}/10</span>}
        <span className={`eligibility-badge eligibility-${job.eligibility}`}>
          {ELIGIBILITY_LABEL[job.eligibility] ?? job.eligibility}
        </span>
      </div>
      <div className="job-card-title">{job.title}</div>
      <div className="job-card-company">{job.company}</div>
      <div className="job-card-location">{job.location}{job.remote ? " · Remote" : ""}</div>
      <div className="job-card-actions" onClick={(e) => e.stopPropagation()}>
        {prevStatus && (
          <button onClick={() => updateStatus.mutate({ id: job.id, status: prevStatus as Status })}>
            ← {prevStatus}
          </button>
        )}
        {nextStatus && (
          <button onClick={() => updateStatus.mutate({ id: job.id, status: nextStatus as Status })}>
            {nextStatus} →
          </button>
        )}
        <button className="job-card-exclude" onClick={handleExclude}>
          ✕ Exclude
        </button>
      </div>
    </div>
  );
}
