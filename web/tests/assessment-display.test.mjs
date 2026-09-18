import assert from "node:assert/strict";
import { test } from "node:test";
import { assessmentDisplay } from "../src/beta/assessmentDisplay.ts";

test("rejected legacy rubric never promotes its saved number", () => {
  for (const rationale of ["The numeric fit estimate is unvalidated, not a pass on any requirement.",
    "The model requirement comparison was discarded because it could not be validated."]) {
    const result = assessmentDisplay({score: 9, rationale});
    assert.match(result, /could not be validated|no match score/i);
    assert.doesNotMatch(result, /9\/10/);
  }
});
test("ordinary estimates remain estimates, not hiring predictions", () => {
  assert.match(assessmentDisplay({score: 7, rationale: "Eligibility requires review."}), /7\/10.*Not an ATS score/);
});
test("missing or malformed scores are not displayed", () => {
  for (const score of [null, undefined, NaN, Infinity, -1, 11]) {
    assert.equal(assessmentDisplay({score, rationale: null}), "No current AI assessment.");
  }
});
