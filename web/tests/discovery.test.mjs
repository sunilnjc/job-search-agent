import assert from "node:assert/strict";
import { test } from "node:test";
import { checkedDiscoveryResponse, discoveryContextKey, discoveryRequest, discoverySavePayload, reusableDiscovery } from "../src/beta/discovery.ts";
import { createMobileTransport } from "../src/beta/mobileTransport.ts";
const job = {
  source_id: "ats_" + "a".repeat(64), source: "greenhouse:clinic", provider: "greenhouse", board: "clinic", external_id: "1",
  title: "Registered Nurse", company_name: "clinic", company_name_is_board_identifier: true,
  source_url: "https://job-boards.greenhouse.io/clinic/jobs/1", description: "Review licence and work authorisation requirements.", location_text: "Toronto", workplace_type: "onsite",
  fetched_at: "2026-09-15T00:00:00Z", content_truncated: false, match_reasons: ["Matches stored title"], persisted: false, eligibility_status: "unknown",
  eligibility: { status: "unknown", provisional: true, independently_verified: false, reasons: ["Requires review"] },
};
const source = { source: job.source, status: "ok", cached: true, fetched_at: job.fetched_at, checked_at: job.fetched_at, truncated: false,
  received_count: 1, returned_count: 1, dropped_count: 0, unlisted_count: 0, duplicate_count: 0 };
const result = { status: "ok", results: [job], sources: [source], partial: false, truncated: false, matched_count: 1, returned_count: 1, searched_at: job.fetched_at, persisted: false, eligibility_verified: false };
const session = { user: { id: "fixture-user" }, access_token: "fixture-token" };

test("recommendations cache is owner/profile/preference-scoped and expires in five minutes", () => {
  const profile = { user_id: "a", career_text: "Confirmed finance experience", career_background: null };
  const preferences = { target_titles: ["Finance Manager"], preferred_locations: ["Dubai"], preferred_regions: [], remote_preference: "open", sponsorship_required: false };
  const key = discoveryContextKey("a", profile, preferences);
  const snapshot = { key, result, receivedAt: 1000 };
  assert.equal(reusableDiscovery(snapshot, key, 2000), result);
  for (const other of [discoveryContextKey("b", profile, preferences), discoveryContextKey("a", {...profile, career_text:"Confirmed nursing experience"}, preferences), discoveryContextKey("a", profile, {...preferences, remote_preference:"remote_only"})]) assert.equal(reusableDiscovery(snapshot, other, 2000), null);
  assert.equal(reusableDiscovery(snapshot, key, 301000), null);
  assert.equal(reusableDiscovery(snapshot, key, 999), null);
});
test("profile relevance is validated and is never converted to verified eligibility", () => {
  const relevance = {method:"profile_rules_v1", score:60, reasons:["Accounting experience appears in this role"], gaps:["Licence needs review"], review_required:true};
  assert.equal(checkedDiscoveryResponse({...result, results:[{...job, relevance}]}).results[0].eligibility_status, "unknown");
  for (const changed of [{...relevance,score:101}, {...relevance,score:NaN}, {...relevance,method:"AI_verified"}, {...relevance,gaps:[{html:"unsafe"}]}]) assert.throws(() => checkedDiscoveryResponse({...result, results:[{...job,relevance:changed}]}));
});

test("empty additional filters defer to server-owned saved preferences without sending profile data", () => {
  assert.deepEqual(discoveryRequest("", "", "", "any"), { query: "", filters: { titles: [], locations: [], workplace_type: "any" }, limit: 20 });
  assert.deepEqual(discoveryRequest("  teaching  ", "Teacher, Art educator", "Toronto, Ottawa", "hybrid").filters, { titles: ["Teacher", "Art educator"], locations: ["Toronto", "Ottawa"], workplace_type: "hybrid" });
});
test("query, filter counts, term size, workplace and UTF-8 request bytes are bounded", () => {
  for (const fields of [["x".repeat(161), "", "", "any"], ["", Array(11).fill("Nurse").join(","), "", "any"], ["", "x".repeat(161), "", "any"], ["", "", "", "worldwide"]]) assert.throws(() => discoveryRequest(...fields));
  const large = Array(10).fill("界".repeat(160)).join(",");
  assert.throws(() => discoveryRequest("", large, large, "any"), /8 KiB/);
});
test("discovery accepts source coverage and remains unsaved and eligibility-unknown", () => {
  assert.deepEqual(checkedDiscoveryResponse(result), result);
  for (const changed of [{ ...result, persisted: true }, { ...result, eligibility_verified: true }, { ...result, results: [{ ...job, eligibility_status: "eligible" }] }, { ...result, results: [{ ...job, source_url: "javascript:alert(1)" }] }, { ...result, results: [{ ...job, source_id: "saved-job-id" }] }]) assert.throws(() => checkedDiscoveryResponse(changed));
});
test("explicit save contains only accepted role fields, never public source_id, owner or eligibility", () => {
  const payload = discoverySavePayload(job);
  assert.deepEqual(Object.keys(payload).sort(), ["company_name", "description", "location_text", "source_url", "title"]);
  assert.equal(payload.description, job.description);
  assert.equal(payload.company_name, "clinic");
});
test("empty success and all-source failure remain distinct statuses", () => {
  assert.equal(checkedDiscoveryResponse({ ...result, results: [], matched_count: 0, returned_count: 0 }).status, "ok");
  assert.equal(checkedDiscoveryResponse({ ...result, status: "unavailable", partial: true, results: [], matched_count: 0, returned_count: 0, sources: [{ ...source, status: "error", fetched_at: null, error_code: "timeout", retry_after: 60 }] }).status, "unavailable");
  assert.throws(() => checkedDiscoveryResponse({ ...result, status: "unavailable" }));
});
test("top-level coverage/geography warnings survive even when no results match", () => {
  const warnings = ["Some saved regions have no supported country map.", "Some postings have unconfirmed geography."];
  assert.deepEqual(checkedDiscoveryResponse({ ...result, results: [], returned_count: 0, matched_count: 0, warnings }).warnings, warnings);
  assert.throws(() => checkedDiscoveryResponse({ ...result, warnings: [{ html: "untrusted" }] }));
});
test("authenticated discovery POST is read-only and does not invoke a save or AI route", async () => {
  const calls = [];
  const request = createMobileTransport(async () => session, async (url, options) => { calls.push([url, options]); return new Response(JSON.stringify(result)); });
  await request("fixture-user", "/discovery/search", { method: "POST", body: discoveryRequest("", "", "", "any") });
  assert.equal(calls.length, 1); assert.equal(calls[0][0], "/api/mobile/discovery/search"); assert.equal(calls[0][1].headers.Authorization, "Bearer fixture-token");
  const offline = createMobileTransport(async () => session, async () => { throw new Error("offline"); });
  await assert.rejects(offline("fixture-user", "/discovery/search", { method: "POST", body: {} }), error => !error.uncertain && /No role was saved/.test(error.message));
});
test("HTTP 503 unavailable envelope retains source diagnostics without a false empty success", async () => {
  const unavailable = { ...result, status: "unavailable", results: [], returned_count: 0, matched_count: 0, sources: [{ ...source, status: "error", error_code: "timeout" }] };
  const request = createMobileTransport(async () => session, async () => new Response(JSON.stringify(unavailable), { status: 503 }));
  await assert.rejects(request("fixture-user", "/discovery/search", { method: "POST" }), error => checkedDiscoveryResponse(error.discoveryResult).sources[0].error_code === "timeout");
});
test("rate-limit guidance preserves Retry-After and does not auto-repeat search", async () => {
  let calls = 0;
  const request = createMobileTransport(async () => session, async () => { calls++; return new Response(JSON.stringify({ detail: { code: "rate_limited", message: "Discovery search limit reached. Retry later." } }), { status: 429, headers: { "Retry-After": "60" } }); });
  await assert.rejects(request("fixture-user", "/discovery/search", { method: "POST" }), error => error.retryAfter === 60 && error.code === "rate_limited");
  assert.equal(calls, 1);
});
