import type { FitExplanation as FitExplanationShape } from "./types";
import { coerceFitExplanation } from "./fitExplanation";

type JobFit = { rationale?: string | null; fit_explanation?: FitExplanationShape | null };

/** Additive structured assessment using existing prose styles. Not a Studio redesign. */
export function FitExplanationBlock({ job }: { job: JobFit }) {
  const fit = coerceFitExplanation(job);
  const sections: Array<[string, string[]]> = [
    ["Why this estimate", fit.why],
    ["Evidence used", fit.evidence],
    ["Uncertainty", fit.uncertainty],
  ];
  const visible = sections.filter(([, items]) => items.length > 0);
  if (!visible.length) {
    return job.rationale ? <p className="workflow-prose">{job.rationale}</p> : null;
  }
  return <>{visible.map(([label, items]) => (
    <p className="workflow-prose" key={label}><strong>{label}.</strong> {items.join(" ")}</p>
  ))}</>;
}
