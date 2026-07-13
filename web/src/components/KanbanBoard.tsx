import { useMemo } from "react";
import type { Job } from "../types";
import { STATUSES } from "../types";
import { JobCard } from "./JobCard";

interface Props {
  jobs: Job[];
  onOpenJob: (id: number) => void;
}

export function KanbanBoard({ jobs, onOpenJob }: Props) {
  const byStatus = useMemo(() => {
    const grouped: Record<string, Job[]> = Object.fromEntries(STATUSES.map((s) => [s, []]));
    for (const job of jobs) {
      if (job.excluded_reason) continue;
      (grouped[job.status] ?? grouped.new).push(job);
    }
    for (const list of Object.values(grouped)) {
      list.sort((a, b) => (b.llm_score ?? -1) - (a.llm_score ?? -1));
    }
    return grouped;
  }, [jobs]);

  return (
    <div className="kanban-board">
      {STATUSES.map((status) => (
        <div key={status} className="kanban-column">
          <div className="kanban-column-header">
            {status} <span className="kanban-count">{byStatus[status]?.length ?? 0}</span>
          </div>
          <div className="kanban-column-body">
            {byStatus[status]?.map((job) => (
              <JobCard key={job.id} job={job} onOpen={onOpenJob} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
