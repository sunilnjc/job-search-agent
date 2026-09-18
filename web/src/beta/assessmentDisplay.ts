import type { BetaJob } from "./types";

// Legacy assessments persist the provider's number even when the server rejects
// its requirement comparison. Keep the diagnostic, but do not promote that
// number as a usable match estimate. This is display-only, never entitlement or
// application-readiness logic.
export function assessmentDisplay(job: Pick<BetaJob, "score" | "rationale">): string {
  const reason = job.rationale || "";
  if (/numeric fit estimate is unvalidated|requirement comparison was discarded/i.test(reason)) {
    return "AI comparison could not be validated, so no match score is shown. Review the posting and any follow-up questions, then assess again.";
  }
  if (job.score == null || !Number.isFinite(job.score) || job.score < 0 || job.score > 10) {
    return "No current AI assessment.";
  }
  return `AI match estimate: ${job.score}/10. Not an ATS score or hiring probability.`;
}
