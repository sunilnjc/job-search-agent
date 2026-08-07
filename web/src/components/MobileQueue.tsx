import { useMemo, useState } from "react";
import type { Job, Status } from "../types";
import type { JobFilters, RoleCategory } from "../filters";
import { ROLE_LABELS, regionOf } from "../filters";
import { useRunTrigger } from "../hooks/useRun";
import { useExcludeJob } from "../hooks/useJobs";

// Phone-first review flow: pick a stage, work down the list, act on each job.
// "drafted" first — that's the queue with materials ready to submit.
const STAGES: { key: Status; label: string }[] = [
  { key: "drafted", label: "Ready" },
  { key: "matched", label: "Matched" },
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
  filters: JobFilters;
  onFiltersChange: (filters: JobFilters) => void;
}

const ROLE_ORDER: Exclude<RoleCategory, "other">[] = ["fde", "senior", "software"];

export function MobileQueue({ jobs, onOpenJob, filters, onFiltersChange }: Props) {
  const [stage, setStage] = useState<Status>("drafted");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const { triggerAutopilot, triggerDirectApply, isTriggering, runStatus } = useRunTrigger();
  const excludeJob = useExcludeJob();
  const processing = isTriggering || runStatus?.status === "running";

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

  const readyCount = counts.drafted ?? 0;
  const hasFilters = filters.roles.size > 0 || filters.regions.size > 0 || Boolean(filters.query);
  const regions = useMemo(() => {
    const countsByRegion = new Map<string, number>();
    for (const job of jobs) {
      const region = regionOf(job);
      countsByRegion.set(region, (countsByRegion.get(region) ?? 0) + 1);
    }
    return [...countsByRegion.entries()].sort((a, b) => b[1] - a[1]);
  }, [jobs]);

  const toggleRole = (role: RoleCategory) => {
    const roles = new Set(filters.roles);
    if (roles.has(role)) {
      roles.delete(role);
    } else {
      roles.add(role);
    }
    onFiltersChange({ ...filters, roles });
  };

  const toggleRegion = (region: string) => {
    const regions = new Set(filters.regions);
    if (regions.has(region)) {
      regions.delete(region);
    } else {
      regions.add(region);
    }
    onFiltersChange({ ...filters, regions });
  };

  return (
    <div className="mq">
      <section className="mq-hero">
        <div>
          <p className="mobile-eyebrow">YOUR APPLICATION DESK</p>
          <h2>{readyCount > 0 ? `${readyCount} ready to review` : "Your queue is clear"}</h2>
          <p>{readyCount > 0 ? "Process the queue once; Telegram will send final-confirmation buttons and group exceptions." : "New matches will appear here after the next job run."}</p>
          {readyCount > 0 && <button className="mq-process" disabled={processing} onClick={() => triggerAutopilot(undefined)}>{processing ? "Preparing queue…" : "Process Ready Queue"}</button>}
          {runStatus && <p className="mq-process-status">{runStatus.log.at(-1) ?? (processing ? "Starting…" : "Done")}</p>}
        </div>
        <button className={hasFilters ? "mq-filter active" : "mq-filter"} onClick={() => setFiltersOpen(true)}>
          Filter{hasFilters ? " · On" : ""}
        </button>
      </section>

      <label className="mq-search">
        <span className="sr-only">Search jobs</span>
        <input
          type="search"
          value={filters.query}
          onChange={(event) => onFiltersChange({ ...filters, query: event.target.value })}
          placeholder="Search jobs, companies, locations"
        />
      </label>

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
                  <button className="mq-card-main" onClick={() => onOpenJob(job.id)}>
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
                  </button>
                  <div className="mq-card-actions">
                    <button className="mq-btn mq-btn-primary" onClick={() => onOpenJob(job.id)}>
                      Review application
                    </button>
                    {stage === "drafted" && (
                      <button className="mq-btn mq-btn-direct" disabled={processing} onClick={() => triggerDirectApply(job.id)}>
                        {processing ? "Preparing…" : "Direct Apply"}
                      </button>
                    )}
                    <button
                      className="mq-btn mq-btn-exclude"
                      disabled={excludeJob.isPending}
                      onClick={() => excludeJob.mutate({ id: job.id, reason: "Not a priority right now" })}
                    >
                      Exclude
                    </button>
                  </div>
                </div>
            );
          })}
        </div>
      )}

      {filtersOpen && (
        <div className="mobile-filter-sheet" role="dialog" aria-modal="true" aria-label="Filters">
          <button className="mobile-filter-backdrop" aria-label="Close filters" onClick={() => setFiltersOpen(false)} />
          <div className="mobile-filter-panel">
            <div className="mobile-filter-header">
              <div>
                <p className="mobile-eyebrow">REFINE QUEUE</p>
                <h2>Filters</h2>
              </div>
              <button className="modal-close" onClick={() => setFiltersOpen(false)}>×</button>
            </div>
            <div className="mobile-filter-section">
              <h3>Role type</h3>
              <div className="mobile-filter-chips">
                {ROLE_ORDER.map((role) => (
                  <button key={role} className={filters.roles.has(role) ? "mobile-filter-chip active" : "mobile-filter-chip"} onClick={() => toggleRole(role)}>
                    {ROLE_LABELS[role]}
                  </button>
                ))}
              </div>
            </div>
            <div className="mobile-filter-section">
              <h3>Region</h3>
              <div className="mobile-filter-chips">
                {regions.map(([region, count]) => (
                  <button key={region} className={filters.regions.has(region) ? "mobile-filter-chip active" : "mobile-filter-chip"} onClick={() => toggleRegion(region)}>
                    {region} <span>{count}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="mobile-filter-actions">
              <button onClick={() => onFiltersChange({ roles: new Set(), regions: new Set(), query: "" })}>Clear all</button>
              <button className="mobile-filter-done" onClick={() => setFiltersOpen(false)}>Show jobs</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
