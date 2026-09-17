import type { FitExplanation } from "./types";

const EVIDENCE_PREFIXES = ["Job posting:", "Confirmed information:", "Additional complete evidence"];
const UNCERTAINTY_MARKERS = [
  "unvalidated", "could not be validated", "needs review", "follow-up", "provisional",
  "unknown", "ineligible", "conflict", "not independently verified", "confirm career",
  "resolve the returned", "mandatory requirement", "not been evaluated",
  "requirement comparison was discarded", "hard constraint",
];

function cleanLines(values: unknown, limit = 16): string[] {
  if (!Array.isArray(values)) return [];
  const items: string[] = [];
  for (const value of values.slice(0, limit)) {
    if (typeof value !== "string") continue;
    const text = value.replace(/\s+/g, " ").trim().slice(0, 2000);
    if (text && !items.includes(text)) items.push(text);
  }
  return items;
}

export function isFitExplanation(value: unknown): value is FitExplanation {
  if (!value || typeof value !== "object") return false;
  const item = value as FitExplanation;
  return [item.why, item.evidence, item.uncertainty].every((field) => Array.isArray(field));
}

export function fitExplanationFromRationale(rationale: string | null | undefined): FitExplanation {
  const why: string[] = [];
  const evidence: string[] = [];
  const uncertainty: string[] = [];
  for (const raw of (rationale || "").split("\n")) {
    const text = raw.replace(/\s+/g, " ").trim();
    if (!text) continue;
    if (EVIDENCE_PREFIXES.some((prefix) => text.startsWith(prefix))) evidence.push(text);
    else if (UNCERTAINTY_MARKERS.some((marker) => text.toLowerCase().includes(marker))) uncertainty.push(text);
    else why.push(text);
  }
  return { why, evidence, uncertainty };
}

export function coerceFitExplanation(job: { rationale?: string | null; fit_explanation?: FitExplanation | null }): FitExplanation {
  const structured = job.fit_explanation;
  if (isFitExplanation(structured) && (structured.why.length || structured.evidence.length || structured.uncertainty.length)) {
    return {
      why: cleanLines(structured.why, 12),
      evidence: cleanLines(structured.evidence, 16),
      uncertainty: cleanLines(structured.uncertainty, 16),
    };
  }
  return fitExplanationFromRationale(job.rationale);
}
