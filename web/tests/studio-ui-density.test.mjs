import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const studio = readFileSync(new URL("../src/beta/ApplicationStudio.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/beta/studio-workflow.css", import.meta.url), "utf8");

test("Application Studio uses plain-language work-rights labels", () => {
  assert.match(studio, /Can you work in this location\?/);
  assert.match(studio, /Job & location/);
  assert.match(studio, /Your answers/);
  assert.match(studio, /Final check/);
  assert.match(studio, /Yes, I can work there/);
  assert.match(studio, /Save answer/);
  assert.match(studio, /Answer from the posting and your work rights/);
  // Primary UI must not lead with self-report / eligibility jargon.
  assert.equal(studio.includes("Work eligibility · your self-report"), false);
  assert.equal(studio.includes("About eligibility self-report"), false);
  assert.equal(studio.includes("Save eligibility self-report"), false);
  assert.equal(studio.includes("Role & eligibility"), false);
  assert.equal(/\bself-report\b/.test(studio), false);
});

test("Application Studio collapses dense essays behind quiet details", () => {
  assert.match(studio, /More about this/);
  assert.match(studio, /studio-about/);
  assert.match(studio, /studio-lede/);
  assert.match(studio, /studio-status-line/);
  assert.match(studio, /studio-helper/);
  // Consent stays visible but shortened; must still mention AI provider send on Assess/Prepare.
  assert.match(studio, /Assess or Prepare may send/);
  assert.match(studio, /configured AI provider/);
  // Old always-visible header essay must not remain as plain text outside details.
  assert.equal(
    studio.includes("Ready requires a current reviewed packet. Submitted is user-reported, not verified by an employer. No automatic submission."),
    false,
  );
  assert.match(studio, /<details className="studio-about">[\s\S]*More about this/);
  assert.match(css, /\.studio-about/);
  assert.match(css, /\.studio-lede/);
  assert.match(css, /\.studio-status-line/);
  assert.match(css, /\.studio-helper/);
  assert.match(css, /\.studio-checklist/);
  assert.match(css, /border-radius:\s*24px/);
});
