import { useMemo, useState } from "react";
import { useJobs } from "./hooks/useJobs";
import { KanbanBoard } from "./components/KanbanBoard";
import { ApplicationsTable } from "./components/ApplicationsTable";
import { ExcludedList } from "./components/ExcludedList";
import { ActionBar } from "./components/ActionBar";
import { FilterBar } from "./components/FilterBar";
import { JobDetailModal } from "./components/JobDetailModal";
import { applyFilters } from "./filters";
import type { JobFilters } from "./filters";
import "./App.css";

type View = "board" | "applications" | "excluded";

const VIEW_LABELS: Record<View, string> = {
  board: "Board",
  applications: "Applications",
  excluded: "Excluded",
};

function App() {
  const { data: jobs, isLoading, error } = useJobs();
  const [openJobId, setOpenJobId] = useState<number | null>(null);
  const [view, setView] = useState<View>("board");
  const [filters, setFilters] = useState<JobFilters>({ roles: new Set(), regions: new Set() });

  const filteredJobs = useMemo(() => (jobs ? applyFilters(jobs, filters) : undefined), [jobs, filters]);

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Job Search Agent</h1>
          <nav className="view-tabs">
            {(Object.keys(VIEW_LABELS) as View[]).map((v) => (
              <button
                key={v}
                className={view === v ? "view-tab active" : "view-tab"}
                onClick={() => setView(v)}
              >
                {VIEW_LABELS[v]}
              </button>
            ))}
          </nav>
        </div>
        <ActionBar />
      </header>

      {jobs && <FilterBar jobs={jobs} filters={filters} onChange={setFilters} />}

      {isLoading && <div className="app-loading">Loading jobs…</div>}
      {error && <div className="app-error">Failed to reach the API: {String(error)}</div>}

      {filteredJobs && view === "board" && <KanbanBoard jobs={filteredJobs} onOpenJob={setOpenJobId} />}
      {filteredJobs && view === "applications" && (
        <ApplicationsTable jobs={filteredJobs} onOpenJob={setOpenJobId} />
      )}
      {filteredJobs && view === "excluded" && <ExcludedList jobs={filteredJobs} onOpenJob={setOpenJobId} />}

      {openJobId !== null && (
        <JobDetailModal jobId={openJobId} onClose={() => setOpenJobId(null)} />
      )}
    </div>
  );
}

export default App;
