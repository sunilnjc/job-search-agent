import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { coerceFitExplanation, fitExplanationFromRationale } from "../src/beta/fitExplanation.ts";
import { isTrustPath, trustPageEnabled } from "../src/beta/trustPage.ts";

const root = dirname(fileURLToPath(import.meta.url));

test("structured fit explanation wins and legacy rationale still splits", () => {
  const structured = coerceFitExplanation({
    rationale: "Fit estimate, not an ATS score.\nConfirmed information: Built reports [career_text.0]",
    fit_explanation: { why: ["Overlap on confirmed clinic work."], evidence: ["Confirmed information: Built reports [career_text.0]"], uncertainty: ["Eligibility unknown."] },
  });
  assert.deepEqual(structured.why, ["Overlap on confirmed clinic work."]);
  assert.equal(structured.evidence[0], "Confirmed information: Built reports");
  assert.equal(structured.uncertainty[0], "Eligibility unknown.");
  const fallback = fitExplanationFromRationale("Fit estimate, not an ATS score.\nConfirmed information: Built reports [career_text.0]\nThe numeric fit estimate is unvalidated.");
  assert.ok(fallback.why.length && fallback.evidence.length && fallback.uncertainty.length);
  assert.equal(fallback.evidence[0], "Confirmed information: Built reports");
  assert.doesNotMatch(fallback.evidence.join(" "), /career_text/);
});

test("trust route is recognized and unpublished by default", () => {
  assert.equal(isTrustPath("/beta/trust"), true);
  assert.equal(isTrustPath("/trust"), true);
  assert.equal(isTrustPath("/beta"), false);
  assert.equal(isTrustPath("/beta/anything"), false);
  assert.equal(trustPageEnabled, false);
});

test("trust page draft copy covers isolation, no auto-submit, export/delete, and AI limits", () => {
  const source = readFileSync(join(root, "../src/beta/TrustSafetyPage.tsx"), "utf8");
  for (const phrase of ["Data isolation", "No automatic employer submit", "Export and delete", "Honest limits of the AI", "does not submit applications"]) {
    assert.match(source, new RegExp(phrase));
  }
});
