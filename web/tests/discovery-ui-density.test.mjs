import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const panel = readFileSync(new URL("../src/beta/DiscoveryPanel.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/beta/discovery.css", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/beta/BetaApp.tsx", import.meta.url), "utf8");

test("Discover keeps honest relevance copy but collapses the essay behind Why this?", () => {
  assert.match(panel, /export const PROVISIONAL_NOTE = "Eligibility and qualifications remain provisional/);
  assert.match(panel, /<details className="discovery-why">[\s\S]*Why this role appeared/);
  assert.match(panel, />Why this\?</);
  assert.match(panel, /Partial coverage:/);
  assert.match(panel, /About these results/);
  assert.match(panel, /Private board check/);
  assert.match(panel, /Preference match is not proof of experience/);
  assert.match(panel, /beta-primary/);
  assert.match(panel, /Save role/);
  assert.match(panel, /Open saved role/);
  assert.equal(panel.includes('discovery-why'), true);
  assert.equal((panel.match(/Why this role appeared/g) || []).length, 1);
  assert.match(css, /\.discovery-fit-line/);
  assert.match(css, /\.discovery-lede/);
  assert.match(app, /Save a role to explore it in Studio/);
});
