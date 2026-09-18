import assert from "node:assert/strict";
import { test } from "node:test";
import {
  eligibilityLabel,
  eligibilityNextAction,
  eligibilityRankWeight,
  sortJobsByEligibility,
} from "../src/beta/eligibilityDisplay.ts";

test("eligibility labels never imply AI confirmation", () => {
  assert.equal(eligibilityLabel("eligible"), "Work rights recorded");
  assert.equal(eligibilityLabel("unknown"), "Work rights not recorded yet");
  assert.match(eligibilityNextAction("unknown"), /AI cannot confirm work rights/);
  assert.match(eligibilityNextAction("needs_review"), /Prepare/);
});

test("rank weight puts work-rights review ahead of fit-ready roles", () => {
  assert.ok(eligibilityRankWeight("needs_review") < eligibilityRankWeight("unknown"));
  assert.ok(eligibilityRankWeight("unknown") < eligibilityRankWeight("eligible"));
  const sorted = sortJobsByEligibility([
    { eligibility_status: "eligible" },
    { eligibility_status: "needs_review" },
    { eligibility_status: "unknown" },
    { eligibility_status: "ineligible" },
  ]);
  assert.deepEqual(sorted.map(j => j.eligibility_status), ["needs_review", "unknown", "eligible", "ineligible"]);
});
