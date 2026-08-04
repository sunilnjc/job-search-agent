import { useMemo, useState } from "react";
import type { Job, Status } from "../types";
import { useExcludeJob, useUpdateStatus } from "../hooks/useJobs";

// Phone-first review flow: pick a stage, work down the list, act on each job.
// "drafted" first — that's the queue with materials ready to submit.
const STAGES: { key: Status; label: string }[] = [
  { key: "drafted", label: "Ready" },
  { key: "matched", label: "Matched" },
  { key: "applied", label: "Applied" },
  { key: "interviewing", label: "Interview" },
  { key: "new", label: "New" },
];

const ELIGIBILITY_LABEL: Record<string, string> = {
  worldwide: "REMOTE ANYWHERE",
  sponsors: "SPONSORS VISA",
  restricted: "RESTRICTED",
  "no-sponsorship": "NO SPONSORSHIP",
  unknown: "",
  "title-filtered": "",
};

interface Props {
  jobs: Job[];
  onOpenJob: (id: number) => void;
}

export function MobileQueue({ jobs, onOpenJob }: Props) {
  const [stage, setStage] = useState<Status>("drafted");
  const updateStatus = useUpdateStatus();
  const excludeJob = useExcludeJob();

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const j of jobs) {
      if (j.excluded_reason) continue;
      c[j.status] = (c[j.status] ?? 0) + 1;
    }
    return c;
  }, [jobs]);

  const visible = useMemo(
    () =>
      jobs
        .filter((j) => !j.excluded_reason && j.status === stage)
        .sort((a, b) => (b.llm_score ?? -1) - (a.llm_score ?? -1)),
    [jobs, stage],
  );

  const handleExclude = (job: Job) => {
    const reason = window.prompt("Why exclude this job?", "Not a fit");
    if (reason && reason.trim()) excludeJob.mutate({ id: job.id, reason: reason.trim() });
  };

  return (
    <div className="mq">
      <div className="mq-stages">
        {STAGES.map((s) => (
          <button
            key={s.key}
            className={stage === s.key ? "mq-stage active" : "mq-stage"}
            onClick={() => setStage(s.key)}
          >
            {s.label}
            <span className="mq-stage-count">{counts[s.key] ?? 0}</span>
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <div className="mq-empty">Nothing in {STAGES.find((s) => s.key === stage)?.label}.</div>
      ) : (
        <div className="mq-list">
          {visible.map((job) => {
            const flag = ELIGIBILITY_LABEL[job.eligibility];
            return (
              <div key={job.id} className="mq-card">
                <div className="mq-card-main" onClick={() => onOpenJob(job.id)}>
                  <div className="mq-card-top">
                    {job.llm_score !== null && (
                      <span className="score-badge">{job.llm_score}/10</span>
                    )}
                    {flag && (
                      <span className={`eligibility-badge eligibility-${job.eligibility}`}>
                        {flag}
                      </span>
                    )}
                  </div>
                  <div className="mq-card-title">{job.title}</div>
                  <div className="mq-card-company">{job.company}</div>
                  <div className="mq-card-location">
                    {job.location}
                    {job.remote ? " · Remote" : ""}
                  </div>
                </div>
                <div className="mq-card-actions">
                  <a
                    className="mq-btn mq-btn-primary"
                    href={job.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open posting ↗
                  </a>
                  {job.status !== "applied" && (
                    <button
                      className="mq-btn"
                      onClick={() => updateStatus.mutate({ id: job.id, status: "applied" })}
                    >
                      ✓ Applied
                    </button>
                  )}
                  {job.status === "applied" && (
                    <button
                      className="mq-btn"
                      onClick={() => updateStatus.mutate({ id: job.id, status: "interviewing" })}
                    >
                      → Interview
                    </button>
                  )}
                  <button className="mq-btn mq-btn-danger" onClick={() => handleExclude(job)}>
                    ✕
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
