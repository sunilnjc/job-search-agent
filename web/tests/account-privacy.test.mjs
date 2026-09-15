import assert from "node:assert/strict";
import { test } from "node:test";
import { canDownloadExport, checkedAccountPrivacy, checkedPrivacyRequest, ERASURE_CONFIRMATION, erasurePayload, pendingPrivacyRequest, privacyStateGuidance } from "../src/beta/accountPrivacy.ts";
import { createMobileTransport, downloadFilename } from "../src/beta/mobileTransport.ts";
const request = { id: "request-1", kind: "export", state: "queued", created_at: "2026-09-15T00:00:00Z" };
const session = { user: { id: "fixture-user" }, access_token: "fixture-bearer" };
const json = body => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });

test("erasure requires exact phrase, explicit acknowledgement and a verified session email", () => {
  for (const [phrase, acknowledged, email] of [["delete my account", true, "fixture@example.test"], ["DELETE MY ACCOUNT ", true, "fixture@example.test"], [ERASURE_CONFIRMATION, false, "fixture@example.test"], [ERASURE_CONFIRMATION, true, null]]) assert.throws(() => erasurePayload(phrase, acknowledged, email));
  assert.deepEqual(erasurePayload(ERASURE_CONFIRMATION, true, "fixture@example.test"), { confirmation: ERASURE_CONFIRMATION, email: "fixture@example.test" });
});
test("only complete and explicitly ready export requests are downloadable", () => {
  for (const state of ["queued", "processing", "blocked", "failed"]) assert.equal(canDownloadExport({ ...request, state, download_ready: true }), false);
  assert.equal(canDownloadExport({ ...request, state: "complete" }), false);
  assert.equal(canDownloadExport({ ...request, state: "complete", download_ready: true }), true);
  assert.equal(canDownloadExport({ ...request, state: "complete", kind: "erase", download_ready: true }), false);
});
test("queued, processing and blocked requests prevent accidental duplicate requests", () => {
  for (const state of ["queued", "processing", "blocked"]) assert.equal(pendingPrivacyRequest({ ...request, state }), true);
  for (const state of ["failed", "complete"]) assert.equal(pendingPrivacyRequest({ ...request, state }), false);
});
test("server states have actionable guidance without independently claiming erasure", () => {
  assert.match(privacyStateGuidance({ ...request, state: "blocked" }), /contact the service operator/);
  assert.match(privacyStateGuidance({ ...request, state: "failed" }), /before deciding whether to submit another request/);
  assert.match(privacyStateGuidance({ ...request, kind: "erase", state: "queued" }), /does not mean the work is complete/);
  assert.match(privacyStateGuidance({ ...request, kind: "erase", state: "complete" }), /does not independently verify deletion/);
});
test("privacy contract validation fails closed for malformed capabilities and request states", () => {
  const payload = { requests: [request], capability: { export: true, erasure: true, processing_configured: false } };
  assert.deepEqual(checkedAccountPrivacy(payload), payload);
  for (const value of [null, {}, { ...payload, capability: { ...payload.capability, export: "true" } }]) assert.throws(() => checkedAccountPrivacy(value));
  for (const value of [{ ...request, state: "deleted" }, { ...request, id: "../profile" }, { ...request, download_ready: "true" }, { ...request, message: {} }]) assert.throws(() => checkedPrivacyRequest(value));
});
test("privacy requests use the authenticated mobile account routes and send no userId", async () => {
  const seen = [];
  const transport = createMobileTransport(async () => session, async (url, options) => { seen.push([url, options]); return json(request); });
  await transport("fixture-user", "/account");
  await transport("fixture-user", "/account/exports", { method: "POST", body: {} });
  await transport("fixture-user", "/account/erasure", { method: "POST", body: erasurePayload(ERASURE_CONFIRMATION, true, "fixture@example.test") });
  assert.deepEqual(seen.map(([url]) => url), ["/api/mobile/account", "/api/mobile/account/exports", "/api/mobile/account/erasure"]);
  assert.equal(seen[1][1].body, "{}");
  assert.deepEqual(Object.keys(JSON.parse(seen[2][1].body)).sort(), ["confirmation", "email"]);
  for (const [, options] of seen) { assert.equal(options.headers.Authorization, "Bearer fixture-bearer"); assert.equal(options.credentials, "omit"); assert.equal(options.redirect, "error"); }
});
test("export download preserves a safe server filename and private bearer transport", async () => {
  const transport = createMobileTransport(async () => session, async (url, options) => {
    assert.equal(url, "/api/mobile/account/exports/request-1/download");
    assert.equal(options.headers.Authorization, "Bearer fixture-bearer");
    return new Response("fixture archive", { headers: { "Content-Type": "application/zip", "Content-Disposition": "attachment; filename*=UTF-8''pursuit%20export.zip" } });
  });
  const result = await transport("fixture-user", "/account/exports/request-1/download", { download: true });
  assert.equal(result.filename, "pursuit export.zip"); assert.equal(await result.blob.text(), "fixture archive");
});
test("unsafe or malformed server filenames fall back instead of creating paths", () => {
  for (const value of [null, 'attachment; filename="../account.zip"', 'attachment; filename="bad\\path.zip"', "attachment; filename*=UTF-8''%00private.zip", "attachment; filename*=UTF-8''%GG"]) assert.equal(downloadFilename(value), null);
  assert.equal(downloadFilename('attachment; filename="pursuit.zip"'), "pursuit.zip");
});
test("an account switch during export body reading withholds private bytes", async () => {
  let reads = 0;
  const transport = createMobileTransport(async () => ++reads < 3 ? session : null, async () => new Response("private fixture"));
  await assert.rejects(transport("fixture-user", "/account/exports/request-1/download", { download: true }), /session changed/);
});
