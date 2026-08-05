import { useMemo } from "react";
import type { Job } from "../types";
import type { JobFilters, RoleCategory } from "../filters";
import { ROLE_LABELS, regionOf } from "../filters";

interface Props {
  jobs: Job[];
  filters: JobFilters;
  onChange: (filters: JobFilters) => void;
}

const ROLE_ORDER: Exclude<RoleCategory, "other">[] = ["fde", "senior", "software"];

export function FilterBar({ jobs, filters, onChange }: Props) {
  const availableRegions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const job of jobs) {
      const region = regionOf(job);
      counts.set(region, (counts.get(region) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [jobs]);

  const toggleRole = (role: RoleCategory) => {
    const next = new Set(filters.roles);
    if (next.has(role)) {
      next.delete(role);
    } else {
      next.add(role);
    }
    onChange({ ...filters, roles: next });
  };

  const toggleRegion = (region: string) => {
    const next = new Set(filters.regions);
    if (next.has(region)) {
      next.delete(region);
    } else {
      next.add(region);
    }
    onChange({ ...filters, regions: next });
  };

  const hasActiveFilters = filters.roles.size > 0 || filters.regions.size > 0 || Boolean(filters.query);

  return (
    <div className="filter-bar">
      <label className="job-search">
        <span className="sr-only">Search jobs</span>
        <input
          type="search"
          value={filters.query}
          onChange={(event) => onChange({ ...filters, query: event.target.value })}
          placeholder="Search title, company, or location"
        />
      </label>
      <div className="filter-group">
        <span className="filter-group-label">Role:</span>
        {ROLE_ORDER.map((role) => (
          <button
            key={role}
            className={filters.roles.has(role) ? "filter-chip active" : "filter-chip"}
            onClick={() => toggleRole(role)}
          >
            {ROLE_LABELS[role]}
          </button>
        ))}
      </div>
      <div className="filter-group">
        <span className="filter-group-label">Region:</span>
        {availableRegions.map(([region, count]) => (
          <button
            key={region}
            className={filters.regions.has(region) ? "filter-chip active" : "filter-chip"}
            onClick={() => toggleRegion(region)}
          >
            {region} <span className="filter-chip-count">{count}</span>
          </button>
        ))}
      </div>
      {hasActiveFilters && (
        <button className="filter-clear" onClick={() => onChange({ roles: new Set(), regions: new Set(), query: "" })}>
          Clear filters
        </button>
      )}
    </div>
  );
}
