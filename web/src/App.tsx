import { useEffect, useMemo, useState } from "react";
import { useJobs } from "./hooks/useJobs";
import { useIsMobile } from "./hooks/useIsMobile";
import { KanbanBoard } from "./components/KanbanBoard";
import { MobileQueue } from "./components/MobileQueue";
import { MobileApplications } from "./components/MobileApplications";
import { MobileExcluded } from "./components/MobileExcluded";
import { MobileNavigation } from "./components/MobileNavigation";
import { ApplicationsTable } from "./components/ApplicationsTable";
import { ExcludedList } from "./components/ExcludedList";
import { ActionBar } from "./components/ActionBar";
import { FilterBar } from "./components/FilterBar";
import { JobDetailModal } from "./components/JobDetailModal";
import { ActionNeededCenter } from "./components/ActionNeededCenter";
import BetaApp from "./beta/BetaApp";
import { betaMode } from "./beta/supabase";
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
  if (betaMode) return <BetaApp />;

  // The public entry point is the multi-user beta. The original personal
  // dashboard deliberately lives at /admin so Cloudflare Access can protect
  // that route without placing its own sign-in page in front of /beta.
  if (window.location.pathname === "/") {
    window.location.replace("/beta");
    return null;
  }

  if (!window.location.pathname.startsWith("/admin")) {
    return <NotFound />;
  }

  return <FounderDashboard />;
}

function FounderDashboard() {
  const { data: jobs, isLoading, error } = useJobs();
  const [openJobId, setOpenJobId] = useState<number | null>(null);
  const [view, setView] = useState<View>("board");
  const [showApprovals, setShowApprovals] = useState(false);
  const [filters, setFilters] = useState<JobFilters>({ roles: new Set(), regions: new Set(), query: "" });
  const isMobile = useIsMobile();

  const filteredJobs = useMemo(() => (jobs ? applyFilters(jobs, filters) : undefined), [jobs, filters]);

  // Telegram's private "Review packet" button opens the existing PWA on the exact job.
  // Keep this query-string based so it also works without a client-side router.
  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get("job");
    const jobId = value ? Number(value) : Number.NaN;
    if (Number.isInteger(jobId) && jobId > 0) setOpenJobId(jobId);
  }, []);

  const closeJob = () => {
    setOpenJobId(null);
    const url = new URL(window.location.href);
    url.searchParams.delete("job");
    window.history.replaceState({}, "", url);
  };

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Job Search Agent</h1>
          <p className="mobile-header-subtitle">Review, tailor, apply — from your phone.</p>
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
        <div className="app-header-actions"><button className="action-needed-trigger" onClick={() => setShowApprovals(true)}>Action needed</button>{!isMobile && <ActionBar />}</div>
      </header>

      {jobs && !isMobile && <FilterBar jobs={jobs} filters={filters} onChange={setFilters} />}

      {isLoading && <div className="app-loading">Loading jobs…</div>}
      {error && <div className="app-error">Failed to reach the API: {String(error)}</div>}

      {filteredJobs &&
        view === "board" &&
        (isMobile ? (
          <MobileQueue
            jobs={filteredJobs}
            onOpenJob={setOpenJobId}
            filters={filters}
            onFiltersChange={setFilters}
          />
        ) : (
          <KanbanBoard jobs={filteredJobs} onOpenJob={setOpenJobId} />
        ))}
      {filteredJobs && view === "applications" && (isMobile ? <MobileApplications jobs={filteredJobs} onOpenJob={setOpenJobId} /> : <ApplicationsTable jobs={filteredJobs} onOpenJob={setOpenJobId} />)}
      {filteredJobs && view === "excluded" && (isMobile ? <MobileExcluded jobs={filteredJobs} onOpenJob={setOpenJobId} /> : <ExcludedList jobs={filteredJobs} onOpenJob={setOpenJobId} />)}

      {openJobId !== null && (
        <JobDetailModal jobId={openJobId} onClose={closeJob} />
      )}
      {showApprovals && <ActionNeededCenter onClose={() => setShowApprovals(false)} />}

      {isMobile && <MobileNavigation view={view} onChange={setView} />}
    </div>
  );
}

function NotFound() {
  return (
    <main className="app-loading">
      <p>This page does not exist.</p>
      <a href="/beta">Go to The Job Pursuit beta</a>
    </main>
  );
}

export default App;
