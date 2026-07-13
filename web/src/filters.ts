import type { Job } from "./types";

export type RoleCategory = "fde" | "senior" | "software" | "other";

export const ROLE_LABELS: Record<Exclude<RoleCategory, "other">, string> = {
  fde: "Forward Deployed Engineer",
  senior: "Senior / Staff Software Engineer",
  software: "Software Engineer",
};

export function categorizeRole(title: string): RoleCategory {
  const t = title.toLowerCase();
  if (t.includes("forward deployed")) return "fde";
  if (t.includes("senior") || t.includes("staff") || t.includes("sr.") || /\bsr\b/.test(t)) return "senior";
  if (t.includes("software engineer") || t.includes("swe")) return "software";
  return "other";
}

// Job locations are messy free text (Adzuna US/DE listings often omit the country
// entirely — just city/county), so country is only reliable when the source recorded
// it directly (Adzuna does, going forward). For everything else, fall back to matching
// well-known place names. Good enough for filtering, not meant to be authoritative.
const REGION_KEYWORDS: [string, string[]][] = [
  ["United Kingdom", ["united kingdom", "uk", "england", "scotland", "wales", "northern ireland",
    "london", "manchester", "birmingham", "edinburgh", "belfast", "glasgow"]],
  ["Germany", ["germany", "deutschland", "münchen", "munich", "berlin", "frankfurt", "hamburg",
    "köln", "cologne", "nordrhein", "bayern", "baden-württemberg", "sachsen", "hessen",
    "niedersachsen", "rheinland", "schleswig", "brandenburg", "thüringen", "saarland"]],
  ["United States", ["united states", "usa", "county", "texas", "california", "new york",
    "washington", "florida", "illinois", "massachusetts", "georgia", "colorado", "oregon",
    "virginia", "pennsylvania", "ohio", "michigan", "d.c."]],
  ["UAE", ["dubai", "abu dhabi", "uae", "united arab emirates", "sharjah"]],
  ["India", ["india", "bangalore", "bengaluru", "mumbai", "hyderabad", "pune", "delhi", "chennai"]],
];

export function regionOf(job: Job): string {
  if (job.country) return job.country;
  const loc = job.location.toLowerCase();
  for (const [region, keywords] of REGION_KEYWORDS) {
    if (keywords.some((kw) => loc.includes(kw))) return region;
  }
  return job.remote ? "Remote (unspecified)" : "Other / Unknown";
}

export interface JobFilters {
  roles: Set<RoleCategory>;
  regions: Set<string>;
}

export function applyFilters(jobs: Job[], filters: JobFilters): Job[] {
  return jobs.filter((job) => {
    if (filters.roles.size > 0 && !filters.roles.has(categorizeRole(job.title))) return false;
    if (filters.regions.size > 0 && !filters.regions.has(regionOf(job))) return false;
    return true;
  });
}
