import type { Job, Status } from "../types";
import { useUpdateStatus } from "../hooks/useJobs";

const APPLICATION_STATUSES: Status[] = ["applied", "interviewing", "rejected", "offer"];

interface Props {
  jobs: Job[];
  onOpenJob: (id: number) => void;
}

export function ApplicationsTable({ jobs, onOpenJob }: Props) {
  const updateStatus = useUpdateStatus();
  const applications = jobs
    .filter((j) => !j.excluded_reason && APPLICATION_STATUSES.includes(j.status))
    .sort((a, b) => APPLICATION_STATUSES.indexOf(a.status) - APPLICATION_STATUSES.indexOf(b.status));

  if (applications.length === 0) {
    return <div className="empty-tab">No applications yet — move a job to "applied" on the board to track it here.</div>;
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Job</th>
          <th>Company</th>
          <th>Location</th>
          <th>Score</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {applications.map((job) => (
          <tr key={job.id} onClick={() => onOpenJob(job.id)}>
            <td>{job.title}</td>
            <td>{job.company}</td>
            <td>{job.location}</td>
            <td>{job.llm_score !== null ? `${job.llm_score}/10` : "—"}</td>
            <td onClick={(e) => e.stopPropagation()}>
              <select
                value={job.status}
                onChange={(e) => updateStatus.mutate({ id: job.id, status: e.target.value as Status })}
              >
                {APPLICATION_STATUSES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
