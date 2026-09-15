import assert from "node:assert/strict";
import { test } from "node:test";
import { createMobileTransport, MobileApiError, MAX_RESUME_BYTES, validateResume, resumePayload } from "../src/beta/mobileTransport.ts";
import { readDraft, saveDraft, clearDraft } from "../src/beta/onboardingDraft.ts";
const account = { access_token: "synthetic-user-token", user: { id: "fixture-user" } };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

test("authenticated transport pins same-origin mobile prefix and never sends cookies or follows redirects", async () => {
  let seen;
  const request = createMobileTransport(async () => account, async (...args) => { seen = args; return json({ ok: true }); });
  assert.deepEqual(await request("fixture-user", "/jobs/fixture-id", { method: "PATCH", body: { description: "Actual posting" } }), { ok: true });
  assert.equal(seen[0], "/api/mobile/jobs/fixture-id");
  assert.equal(seen[1].headers.Authorization, "Bearer synthetic-user-token");
  assert.equal(seen[1].credentials, "omit"); assert.equal(seen[1].redirect, "error"); assert.equal(seen[1].cache, "no-store");
});
test("no founder, absolute, encoded or traversal endpoint can receive a token", async () => {
  let calls = 0;
  const request = createMobileTransport(async () => account, async () => { calls++; return json({}); });
  for (const path of ["/api/jobs", "https://example.invalid", "//example.invalid", "/jobs/../profile", "/jobs/%2e%2e", "/jobs/a?url=evil", "/jobs/a\\b"]) await assert.rejects(request("fixture-user", path), /Invalid private/);
  assert.equal(calls, 0);
});
test("missing or different account rejects before any request", async () => {
  for (const session of [null, { ...account, user: { id: "another-user" } }]) {
    const request = createMobileTransport(async () => session, async () => { assert.fail("network must not run"); });
    await assert.rejects(request("fixture-user", "/bootstrap"), error => error.status === 401);
  }
});
test("a fresh session token is obtained per action", async () => {
  let token = "first";
  const seen = [];
  const request = createMobileTransport(async () => ({ ...account, access_token: token }), async (_, options) => { seen.push(options.headers.Authorization); return json({}); });
  await request("fixture-user", "/bootstrap"); token = "refreshed"; await request("fixture-user", "/bootstrap");
  assert.deepEqual(seen, ["Bearer first", "Bearer refreshed"]);
});
test("late response after account switch is withheld", async () => {
  let reads = 0;
  const request = createMobileTransport(async () => ++reads === 1 ? account : null, async () => json({ private: true }));
  await assert.rejects(request("fixture-user", "/bootstrap"), /interrupted/);
});
test("missing-facts 422 carries questions to the UI", async () => {
  const questions = [{ id: "q", job_id: "job", prompt: "Confirm dates", status: "pending" }];
  const request = createMobileTransport(async () => account, async () => json({ detail: { message: "Need your facts", questions } }, 422));
  await assert.rejects(request("fixture-user", "/jobs/job/prepare", { method: "POST", body: {} }), error => error instanceof MobileApiError && error.message === "Need your facts" && error.questions[0].id === "q");
});
test("artifact recovery code and operation ID survive error parsing", async () => {
  const request = createMobileTransport(async () => account, async () => json({ detail: { message: "Saved bytes need reconciliation", code: "artifact_recovery_required", operation_id: "pending-id" } }, 503));
  await assert.rejects(request("fixture-user", "/jobs/job/prepare", { method: "POST" }), error => error.code === "artifact_recovery_required" && error.operationId === "pending-id");
});
test("saved-sync-pending is a successful result, not an error or automatic regeneration", async () => {
  let calls = 0;
  const request = createMobileTransport(async () => account, async () => { calls++; return json({ artifacts: [], operation_status: "saved_sync_pending", warnings: ["Refresh first"] }); });
  assert.equal((await request("fixture-user", "/jobs/job/prepare", { method: "POST" })).operation_status, "saved_sync_pending");
  assert.equal(calls, 1);
});
test("401, 403 and 429 are actionable without auto-retry", async () => {
  for (const [status, phrase] of [[401, "session expired"], [403, "cannot access"], [429, "limit was reached"]]) {
    let calls = 0;
    const request = createMobileTransport(async () => account, async () => { calls++; return json({}, status); });
    await assert.rejects(request("fixture-user", "/bootstrap"), error => error.status === status && error.message.includes(phrase));
    assert.equal(calls, 1);
  }
});
test("an interrupted write is uncertain, with no retry", async () => {
  let calls = 0;
  const request = createMobileTransport(async () => account, async () => { calls++; throw new Error("offline"); });
  await assert.rejects(request("fixture-user", "/resumes", { method: "POST" }), error => error.uncertain && /may have completed/.test(error.message));
  assert.equal(calls, 1);
});
test("quota guidance is not mistaken for revoked workspace access or a short request throttle", async () => {
  for (const [status, message] of [[403, "AI quota is not provisioned or has expired. Contact the operator."],
    [429, "Your AI budget is exhausted. Retry after the budget resets or contact the operator."]]) {
    let calls = 0;
    const request = createMobileTransport(async () => account, async () => { calls++; return json({ detail: message }, status); });
    await assert.rejects(request("fixture-user", "/jobs/job/rank", { method: "POST" }), error => error.status === status && error.message === message);
    assert.equal(calls, 1);
  }
});
test("timeout aborts an in-flight request", async () => {
  const request = createMobileTransport(async () => account, async (_, options) => new Promise((_, reject) => options.signal.addEventListener("abort", () => reject(new Error("aborted")))));
  await assert.rejects(request("fixture-user", "/bootstrap", { timeoutMs: 10 }), /Could not load/);
});
test("private download returns bytes via the same authenticated proxy", async () => {
  const request = createMobileTransport(async () => account, async (_, options) => { assert.equal(options.headers.Authorization, "Bearer synthetic-user-token"); return new Response("fixture bytes"); });
  assert.equal(await (await request("fixture-user", "/artifacts/id/download", { blob: true })).text(), "fixture bytes");
});
test("resume format and 8 MiB limit reject DOC, empty, path and control filenames before upload", () => {
  for (const [name, size] of [["legacy.doc", 10], ["empty.pdf", 0], ["large.docx", MAX_RESUME_BYTES + 1], ["../bad.pdf", 10], ["bad\0.pdf", 10], ["a".repeat(180) + ".pdf", 10]]) assert.throws(() => validateResume({ name, size }));
  validateResume({ name: "Résumé.PDF", size: MAX_RESUME_BYTES }); validateResume({ name: "source.docx", size: 1 });
});
test("resume payload is bounded JSON; server remains responsible for file-content validation", async () => {
  const data = await resumePayload(new File(["synthetic bytes"], "source.pdf"));
  assert.equal(data.filename, "source.pdf"); assert.equal(atob(data.content_base64), "synthetic bytes");
});
test("per-upload idempotency UUID is preserved on the private request", async () => {
  const key = "00000000-0000-4000-8000-000000000001";
  const request = createMobileTransport(async () => account, async (_, options) => { assert.equal(options.headers["Idempotency-Key"], key); return json({}); });
  await request("fixture-user", "/resumes", { method: "POST", idempotencyKey: key });
});
const fields = { displayName: "Fixture", baseLocation: "", targetTitles: "Designer", preferredLocations: "", preferredRegions: "", remotePreference: "open", sponsorshipRequired: false, workAuthorizationNotes: "", careerText: "Confirmed synthetic facts", factsConfirmed: true };
const draft = { version: 1, step: 4, fields, fileName: "fixture.pdf", uploadedResumeId: "saved-id", uploadUncertain: false };
const memoryStorage = () => { const values = new Map(); return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }; };
test("draft and confirmed upload receipt survive reload, are account scoped, and clear on completion", () => {
  const storage = memoryStorage(); assert.equal(saveDraft(storage, "a", draft), true);
  assert.deepEqual(readDraft(storage, "a"), draft); assert.equal(readDraft(storage, "b"), null);
  clearDraft(storage, "a"); assert.equal(readDraft(storage, "a"), null);
});
test("malformed or unavailable draft storage falls back safely", () => {
  const storage = { getItem: () => "not json", setItem: () => { throw new Error("full"); }, removeItem: () => { throw new Error("blocked"); } };
  assert.equal(readDraft(storage, "a"), null); assert.equal(saveDraft(storage, "a", draft), false); clearDraft(storage, "a");
});
