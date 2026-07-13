import type { Job } from "../types";
import { useUnexcludeJob } from "../hooks/useJobs";

interface Props {
  jobs: Job[];
  onOpenJob: (id: number) => void;
}

export function ExcludedList({ jobs, onOpenJob }: Props) {
  const unexcludeJob = useUnexcludeJob();
  const excluded = jobs.filter((j) => j.excluded_reason);

  if (excluded.length === 0) {
    return <div className="empty-tab">No excluded jobs — use the "✕ Exclude" button on a job card to file one here with a reason.</div>;
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Job</th>
          <th>Company</th>
          <th>Location</th>
          <th>Reason</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {excluded.map((job) => (
          <tr key={job.id} onClick={() => onOpenJob(job.id)}>
            <td>{job.title}</td>
            <td>{job.company}</td>
            <td>{job.location}</td>
            <td className="excluded-reason-cell">{job.excluded_reason}</td>
            <td onClick={(e) => e.stopPropagation()}>
              <button onClick={() => unexcludeJob.mutate(job.id)}>Restore to board</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
