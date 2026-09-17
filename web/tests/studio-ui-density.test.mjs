import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const studio = readFileSync(join(root, "src/beta/ApplicationStudio.tsx"), "utf8");
const css = readFileSync(join(root, "src/beta/studio-workflow.css"), "utf8");

test("studio densifies Ready/Submitted essay behind disclosure", () => {
  assert.match(studio, /About Ready and Submitted/);
  assert.match(studio, /studio-status-line/);
  assert.doesNotMatch(
    studio,
    /<p>Application record:.*Ready requires a current reviewed packet\. Submitted is user-reported/,
  );
});

test("studio hides long eligibility and rationale copy by default", () => {
  assert.match(studio, /About eligibility self-report/);
  assert.match(studio, /Why this estimate\?/);
  assert.match(studio, /About packet binding and Ready/);
  assert.match(studio, /About missing bytes and new paid preparation/);
});

test("studio quiet disclosure styles exist", () => {
  assert.match(css, /\.studio-about/);
  assert.match(css, /\.studio-lede/);
  assert.match(css, /\.studio-status-line/);
});
