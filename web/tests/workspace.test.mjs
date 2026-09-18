import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { activeRoles, applicationCounts, groupRoles, safePostingUrl, WORKSPACE_TAB_LABELS, WORKSPACE_TABS } from "../src/beta/workspace.ts";

const job = (id, status = "new", eligibility_status = "unknown") => ({ id, status, eligibility_status });

test("web navigation matches Discover → Rank → Prepare → Review", () => {
  assert.deepEqual(WORKSPACE_TABS, ["discover", "rank", "prepare", "review"]);
  assert.equal(WORKSPACE_TAB_LABELS.prepare, "Prepare");
  assert.equal(WORKSPACE_TAB_LABELS.review, "Review");
});

test("review and ready groups never duplicate a role", () => {
  const groups = groupRoles([job("review", "ready", "needs_review"), job("ready", "ready", "eligible"), job("saved")]);
  assert.deepEqual(groups.review.map(j => j.id), ["review"]);
  assert.deepEqual(groups.ready.map(j => j.id), ["ready"]);
  assert.deepEqual(groups.saved.map(j => j.id), ["saved"]);
});

test("terminal job labels do not appear in active discovery", () => {
  assert.deepEqual(activeRoles([job("a", "applied"), job("b", "excluded"), job("c", "archived"), job("d", "matched")]).map(j => j.id), ["d"]);
});

test("unknown eligibility remains visible and is never inferred eligible", () => {
  const groups = groupRoles([job("unknown"), job("known-mismatch", "new", "ineligible")]);
  assert.equal(groups.saved.length, 2);
  assert.equal(groups.saved[0].eligibility_status, "unknown");
});

test("tracker counts use application records, including interview and closed history", () => {
  const applications = ["draft", "ready", "submitted", "interviewing", "rejected", "withdrawn", "closed"].map(status => ({ status }));
  assert.deepEqual(applicationCounts(applications), { draft: 2, submitted: 1, interviewing: 1, active: 4 });
  assert.deepEqual(applicationCounts([]), { draft: 0, submitted: 0, interviewing: 0, active: 0 });
});

test("posting links accept HTTP(S) but reject script URLs and embedded credentials", () => {
  assert.equal(safePostingUrl(" https://careers.example.com/job?id=123 "), "https://careers.example.com/job?id=123");
  assert.equal(safePostingUrl("http://example.com"), "http://example.com/");
  for (const value of ["javascript:alert(1)", "data:text/html,test", "/relative", "https://user:password@example.com", "invalid"]) assert.equal(safePostingUrl(value), null);
});

const css = readFileSync(new URL("../src/beta/pursuit-theme.css", import.meta.url), "utf8");
const swift = readFileSync(new URL("../../ios/JobPursuit/Sources/ColorTokens.swift", import.meta.url), "utf8");
const tokenMap = { ink: "text", muted: "muted", paper: "bg", card: "surface", subtle: "surface-strong" };
for (const scheme of ["light", "dark"]) {
  test(`${scheme} web neutral palette stays in sync with native iOS`, () => {
    const native = swift.match(new RegExp(`static let ${scheme} = PursuitColorScheme\\(([\\s\\S]*?)\\)`))[1];
    const section = scheme === "light" ? css.split("@media")[0] : css.split("@media (prefers-color-scheme: dark)")[1].split("body:has")[0];
    for (const [name, webName] of Object.entries(tokenMap)) {
      const value = native.match(new RegExp(`${name}: 0x([A-Fa-f0-9]{6})`))[1];
      assert.ok(section.includes(`--beta-${webName}: #${value};`), `${scheme} ${name} must match iOS`);
    }
  });
}

function luminance(hex) {
  const channels = hex.match(/\w\w/g).map(v => parseInt(v, 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
}
test("normal-size text pairs meet WCAG AA contrast in both themes", () => {
  const pairs = [
    ["232426", "F4F0E8"], ["575B61", "F4F0E8"], ["575B61", "FFFFFF"], ["575B61", "ECECED"],
    ["F4F4F5", "111218"], ["BFC2C8", "111218"], ["BFC2C8", "1C1D22"], ["BFC2C8", "26272D"],
    ["FFFFFF", "6640E0"], ["D1D5DE", "233238"], ["233238", "A8B5A2"], ["233238", "F4F0E8"],
    ["A32335", "FFF0F2"], ["FFBAC4", "351B24"], ["245D40", "EAF5ED"], ["BFE8CD", "172E24"],
  ];
  for (const [fg, bg] of pairs) {
    const values = [luminance(fg), luminance(bg)].sort((a,b) => b-a);
    assert.ok((values[0]+.05)/(values[1]+.05) >= 4.5, `Insufficient contrast: ${fg}/${bg}`);
  }
});
