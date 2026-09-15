import type { BetaApplication, BetaJob } from "./types.ts";

export const WORKSPACE_TABS = ["today", "discover", "studio", "tracker"] as const;
export type WorkspaceTab = typeof WORKSPACE_TABS[number];

export function activeRoles(jobs: BetaJob[]) {
  return jobs.filter(job => !["excluded", "archived", "applied"].includes(job.status));
}

export function groupRoles(jobs: BetaJob[]) {
  const active = activeRoles(jobs);
  // Each role appears once. Eligibility uncertainty is not a verified mismatch.
  const review = active.filter(job => job.eligibility_status === "needs_review");
  const ready = active.filter(job => job.status === "ready" && job.eligibility_status !== "needs_review");
  const saved = active.filter(job => job.status !== "ready" && job.eligibility_status !== "needs_review");
  return { review, ready, saved };
}

export function applicationCounts(applications: BetaApplication[]) {
  return {
    draft: applications.filter(item => item.status === "draft" || item.status === "ready").length,
    submitted: applications.filter(item => item.status === "submitted").length,
    interviewing: applications.filter(item => item.status === "interviewing").length,
    active: applications.filter(item => !["rejected", "withdrawn", "closed"].includes(item.status)).length,
  };
}

export function safePostingUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
