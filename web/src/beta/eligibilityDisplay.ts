import type { BetaJob } from "./types";

/** Plain-language work-rights labels. Never imply AI can confirm eligibility. */
export function eligibilityLabel(status: BetaJob["eligibility_status"]): string {
  const labels: Record<BetaJob["eligibility_status"], string> = {
    eligible: "Work rights recorded",
    ineligible: "Not currently eligible",
    needs_review: "Work rights need your review",
    unknown: "Work rights not recorded yet",
  };
  return labels[status];
}

/** Next action for rank / prepare surfaces. */
export function eligibilityNextAction(status: BetaJob["eligibility_status"]): string {
  if (status === "eligible") {
    return "Work-rights self-report is saved. Re-check if the posting or your situation changes.";
  }
  if (status === "ineligible") {
    return "You recorded that you are not eligible. Update that self-report in Prepare if it changes.";
  }
  if (status === "needs_review") {
    return "Open Prepare and answer the work-rights question for this posting before you apply.";
  }
  return "Open Prepare to record whether you can work in this location. AI cannot confirm work rights.";
}

/** Sort key: needs_review first, then unknown, then eligible, then ineligible. */
export function eligibilityRankWeight(status: BetaJob["eligibility_status"]): number {
  const weights: Record<BetaJob["eligibility_status"], number> = {
    needs_review: 0,
    unknown: 1,
    eligible: 2,
    ineligible: 3,
  };
  return weights[status];
}

export function sortJobsByEligibility(jobs: BetaJob[]): BetaJob[] {
  return [...jobs].sort((a, b) => eligibilityRankWeight(a.eligibility_status) - eligibilityRankWeight(b.eligibility_status));
}
