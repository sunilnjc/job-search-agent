import assert from "node:assert/strict";
import { test } from "node:test";
import { checkedReadiness, durablePacketReviewed, latestApplication, packetReview, readinessIssue, readinessReadIssue, refreshAfterApplicationSave, reviewMatches, serverStudioPackets, studioContextKey, studioPackets } from "../src/beta/studioReadiness.ts";

const generation = "00000000-0000-4000-8000-000000000001";
const otherGeneration = "00000000-0000-4000-8000-000000000002";
const stamp = "20260915T120000123456Z";
const mimes = { pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" };
function file(kind, format = "pdf", overrides = {}, version = generation) {
  return { id: `${version}-${kind}-${format}`, job_id: "job", resume_id: "source", kind,
    filename: `${kind}-role_aligned-${stamp}-${version}.${format}`, mime_type: mimes[format], byte_size: 100,
    created_at: "2026-09-15T12:00:01Z", ...overrides };
}
const pair = () => [file("tailored_resume"), file("cover_letter")];
const packets = (artifacts = pair()) => studioPackets(artifacts, "job", "source", "role_aligned");
function fixture() {
  const job = { id: "job", title: "Designer", description: "Design accessible tools.", company_name: "Fixture",
    source_url: "https://example.invalid/jobs/1", location_text: "Remote", workplace_type: "remote", status: "new",
    eligibility_review: { status: "eligible", confirmed: true, reason: "Synthetic self-report", confirmed_at: "2026-09-15T11:00:00Z" } };
  return { job, workspace: { profile: { user_id: "owner", display_name: "Fixture", career_text: "Designed accessible tools.", career_background: { qualifications: [] } },
    preferences: { user_id: "owner", target_titles: ["Designer"], remote_preference: "remote_only", minimum_match_score: 7 },
    resumes: [{ id: "source", original_filename: "source.pdf", byte_size: 100 }], artifacts: pair(), applications: [], jobs: [job],
    questions: [], capabilities: { bootstrap_list_limit: 200 } } };
}
const key = (workspace, job, owner = "owner", source = "source", variant = "role_aligned") => studioContextKey(owner, workspace, job, source, variant);

test("four server-named artifacts form one generation with an exact PDF pair", () => {
  const rows = [...pair(), file("tailored_resume", "docx"), file("cover_letter", "docx")];
  const [packet] = packets(rows);
  assert.equal(packets(rows).length, 1);
  assert.equal(packet.artifacts.length, 4);
  assert.deepEqual(packet.pair.map(a => a.kind), ["tailored_resume", "cover_letter"]);
  assert.ok(packet.pair.every(a => a.filename.endsWith(".pdf")));
  assert.equal(packet.generatedAt, "2026-09-15T12:00:00.123Z");
});
test("never mix historical generations even with the same timestamp/source", () => {
  const rows = [file("tailored_resume"), file("cover_letter", "pdf", {}, otherGeneration)];
  assert.equal(packets(rows).length, 2);
  assert.ok(packets(rows).every(p => p.pair === null && p.issue));
});
test("same UUID with different generation timestamps is not paired", () => {
  const letter = file("cover_letter"); letter.filename = letter.filename.replace(stamp, "20260915T120000123457Z");
  assert.ok(packets([file("tailored_resume"), letter]).every(p => !p.pair));
});
test("source, job and preparation approach each isolate packets", () => {
  for (const change of [{ resume_id: "different" }, { resume_id: null }, { job_id: "different" },
    { filename: file("cover_letter").filename.replace("role_aligned", "career_change") }]) {
    assert.equal(packets([file("tailored_resume"), file("cover_letter", "pdf", change)])[0].pair, null);
  }
});
test("legacy/proximity/MIME/kind/invalid-date guesses cannot establish a generation", () => {
  for (const change of [{ filename: "cover-letter.pdf" }, { mime_type: "text/html" }, { kind: "answer_packet" },
    { filename: file("cover_letter").filename.replace("20260915", "20260230") }, { byte_size: 0 },
    { filename: file("cover_letter").filename.replace(generation, "not-a-generation") }]) {
    const result = packets([file("tailored_resume"), file("cover_letter", "pdf", change)]);
    assert.ok(result.every(p => p.pair === null));
  }
});
test("duplicate file slots/IDs are ambiguous, not a best-effort choice", () => {
  for (const extra of [file("tailored_resume"), file("tailored_resume", "pdf", { id: "duplicate-slot" })]) {
    const [packet] = packets([...pair(), extra]);
    assert.equal(packet.pair, null); assert.match(packet.issue, /Ambiguous/);
  }
});
test("DOCX-only pair is supported, unlike a mixed-format incomplete generation", () => {
  assert.ok(packets([file("tailored_resume", "docx"), file("cover_letter", "docx")])[0].pair);
  assert.equal(packets([file("tailored_resume"), file("cover_letter", "docx")])[0].pair, null);
});
test("packet construction is order independent and does not mutate artifacts", () => {
  const rows = pair(), saved = structuredClone(rows);
  assert.deepEqual(packets(rows), packets([...rows].reverse())); assert.deepEqual(rows, saved);
});
test("review binds selected generation and exact chosen artifact metadata", () => {
  const [packet] = packets(), approval = packetReview(packet, "context");
  assert.equal(reviewMatches(approval, packet, "context"), true);
  assert.equal(reviewMatches(approval, packet, "new-context"), false);
  assert.equal(reviewMatches(approval, undefined, "context"), false);
  assert.equal(reviewMatches(approval, packets([file("tailored_resume", "pdf", {}, otherGeneration), file("cover_letter", "pdf", {}, otherGeneration)])[0], "context"), false);
  for (const change of [{ id: "replaced" }, { byte_size: 200 }, { created_at: "2026-09-16T12:00:00Z" }]) {
    assert.equal(reviewMatches(approval, packets([file("tailored_resume", "pdf", change), file("cover_letter")])[0], "context"), false);
  }
});
test("changed career facts, qualifications, preferences, job facts and answers invalidate review", () => {
  const { workspace, job } = fixture();
  const baseline = key(workspace, job);
  const edits = [w => { w.profile.career_text += " Changed."; }, w => { w.profile.phone = "synthetic"; },
    w => { w.profile.career_background.qualifications = [{ name: "Example", status: "expired" }]; },
    w => { w.preferences.target_titles.push("Architect"); }, w => { w.preferences.minimum_match_score = 8; },
    w => { w.resumes[0].byte_size++; }, w => { w.questions.push({ id: "q", job_id: "job", prompt: "Confirm?", answer: "No", status: "answered", remember: false }); }];
  for (const edit of edits) { const changed = structuredClone(workspace); edit(changed); assert.notEqual(key(changed, job), baseline); }
  for (const change of [{ description: "New requirements" }, { company_name: "Another employer" }, { workplace_type: "onsite" },
    { eligibility_review: { ...job.eligibility_review, status: "unknown" } }]) assert.notEqual(key(workspace, { ...job, ...change }), baseline);
  assert.notEqual(key(workspace, job, "other-owner"), baseline);
  assert.notEqual(key(workspace, job, "owner", "other-source"), baseline);
  assert.notEqual(key(workspace, job, "owner", "source", "career_change"), baseline);
});
test("status/score/list ordering are not generation facts; foreign questions do not affect review", () => {
  const { workspace, job } = fixture(), baseline = key(workspace, job);
  const changed = structuredClone(workspace);
  changed.applications = [{ id: "app", job_id: "job", status: "ready" }];
  changed.questions = [{ id: "foreign", job_id: "other-job", answer: "not relevant" }];
  changed.profile.updated_at = "2026-09-16T00:00:00Z";
  assert.equal(key(changed, { ...job, score: 9, rationale: "new estimate", status: "ready", updated_at: "new" }), baseline);
  const reordered = { ...workspace, profile: Object.fromEntries(Object.entries(workspace.profile).reverse()) };
  assert.equal(key(reordered, job), baseline);
});
test("global and job answer changes invalidate the snapshot; answer ordering does not", () => {
  const { workspace, job } = fixture();
  workspace.questions = [{ id: "b", job_id: "job", prompt: "Work?", answer: "No", status: "answered" }, { id: "a", job_id: null, prompt: "Career?", answer: "Design", status: "answered" }];
  const baseline = key(workspace, job);
  assert.equal(key({ ...workspace, questions: [...workspace.questions].reverse() }, job), baseline);
  workspace.questions[0].answer = "Yes";
  assert.notEqual(key(workspace, job), baseline);
});
test("capped, missing and ambiguous list reads cannot prove current readiness", () => {
  const { workspace } = fixture(); assert.equal(readinessReadIssue(workspace), null);
  for (const name of ["artifacts", "resumes", "applications", "questions"]) {
    for (const value of [null, Array.from({ length: 200 }, (_, i) => ({ id: `${i}` })), [{ id: "same" }, { id: "same" }]]) {
      assert.match(readinessReadIssue({ ...workspace, [name]: value }), /incomplete|ambiguous/);
    }
  }
  assert.ok(readinessReadIssue({ ...workspace, capabilities: { bootstrap_list_limit: 0 } }));
});
test("coherent packet still requires current source, facts, eligible self-report and resolved questions", () => {
  const { workspace, job } = fixture(), [packet] = packets();
  assert.equal(readinessIssue(workspace, job, "source", packet), null);
  assert.match(readinessIssue(workspace, job, "missing", packet), /source resume/);
  assert.match(readinessIssue(workspace, job, "source", undefined), /Select one/);
  assert.match(readinessIssue(workspace, { ...job, eligibility_review: null }, "source", packet), /self-report/);
  assert.match(readinessIssue({ ...workspace, questions: [{ id: "q", job_id: null, status: "pending" }] }, job, "source", packet), /questions/);
  assert.match(readinessIssue({ ...workspace, profile: { ...workspace.profile, career_text: "" } }, job, "source", packet), /career facts/);
});
test("confirmed application save always attempts both refreshes, even for synchronous failure", async () => {
  const calls = [];
  const results = await refreshAfterApplicationSave(() => { calls.push("studio"); throw new Error("offline fixture"); }, async () => { calls.push("parent"); });
  assert.deepEqual(calls, ["studio", "parent"]); assert.deepEqual(results.map(r => r.status), ["rejected", "fulfilled"]);
});
test("parent refresh failure does not hide successful Studio refresh", async () => {
  const results = await refreshAfterApplicationSave(async () => "refreshed", async () => { throw new Error("offline fixture"); });
  assert.deepEqual(results.map(r => r.status), ["fulfilled", "rejected"]);
});
test("acknowledged application status survives stale reads, but accepts a newer server status", () => {
  const saved = { id: "app", job_id: "job", status: "ready", updated_at: "2026-09-15T12:00:00Z" };
  assert.equal(latestApplication([], "job", saved), saved);
  assert.equal(latestApplication([{ ...saved, status: "draft", updated_at: "2026-09-14T12:00:00Z" }], "job", saved), saved);
  assert.equal(latestApplication([{ ...saved, status: "draft" }], "job", saved), saved);
  assert.equal(latestApplication([{ ...saved, status: "submitted", updated_at: "2026-09-16T12:00:00Z" }], "job", saved).status, "submitted");
  assert.equal(latestApplication([], "different-job", saved), undefined);
});

const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
function durableFixture() {
  const packet = { key: id(30) + ":pdf", run_id: id(30), resume_id: id(20), variant: "role_aligned", format: "pdf",
    generated_at: "2026-09-15T12:00:00Z", current: true, issue: null,
    context_fingerprint: "a".repeat(64), packet_fingerprint: "b".repeat(64), source_sha256: "c".repeat(64),
    artifacts: ["tailored_resume", "cover_letter"].map((kind, i) => ({
      id: id(40 + i), job_id: id(10), resume_id: id(20), kind, filename: `${kind}.pdf`, mime_type: mimes.pdf,
      byte_size: 100, created_at: "2026-09-15T12:00:00Z", sha256: "d".repeat(64),
    })) };
  return { user_id: id(1), job_id: id(10), version: "packet-v1", packets: [packet], review: null, ready: false,
    reason: "packet_review_required", pending_questions: 0, application_status: "draft", recorded_status: null, application_id: null };
}
function withReview() {
  const s = durableFixture(), p = s.packets[0];
  return { ...s, ready: true, reason: null, application_status: "ready", recorded_status: "ready", application_id: id(50),
    review: { id: id(60), run_id: p.run_id, resume_artifact_id: p.artifacts[0].id, letter_artifact_id: p.artifacts[1].id,
      packet_fingerprint: p.packet_fingerprint, reviewed_at: "2026-09-15T12:05:00Z", current: true } };
}

test("server generation identity works without filename guesses and does not fallback to legacy filenames", () => {
  const s = checkedReadiness(durableFixture(), id(1), id(10));
  const [packet] = serverStudioPackets(s, id(20), "role_aligned");
  assert.equal(packet.key, id(30)+":pdf"); assert.ok(packet.server.current); assert.equal(packet.pair.length, 2);
  assert.equal(studioPackets(packet.artifacts, id(10), id(20), "role_aligned").length, 0);
  assert.deepEqual(serverStudioPackets(null, id(20), "role_aligned"), []);
  assert.deepEqual(serverStudioPackets(s, id(21), "role_aligned"), []);
});
test("saved current receipt survives a new client session only for its exact packet", () => {
  const s = checkedReadiness(withReview(), id(1), id(10));
  assert.equal(durablePacketReviewed(s, s.packets[0]), true);
  for (const change of [{ packet_fingerprint: "f".repeat(64) }, { run_id: id(31) }, { current: false },
    { artifacts: [{ ...s.packets[0].artifacts[0], id: id(99) }, s.packets[0].artifacts[1]] }]) {
    assert.equal(durablePacketReviewed(s, { ...s.packets[0], ...change }), false);
  }
  assert.equal(durablePacketReviewed({ ...s, ready: false }, s.packets[0]), false);
});
test("server-stale generation stays downloadable but cannot be reviewed", () => {
  const s = durableFixture(); s.packets[0].current = false; s.packets[0].issue = "generation_context_changed";
  const [packet] = serverStudioPackets(checkedReadiness(s, id(1), id(10)), id(20), "role_aligned");
  assert.equal(packet.pair.length, 2); assert.match(packet.issue, /no longer matches/);
  assert.equal(packetReview(packet, "context"), null);
});
test("local pending review is fenced by the server packet fingerprint", () => {
  const s = durableFixture(), [packet] = serverStudioPackets(s, id(20), "role_aligned");
  const review = packetReview(packet, "context"); assert.equal(reviewMatches(review, packet, "context"), true);
  s.packets[0].packet_fingerprint = "f".repeat(64);
  assert.equal(reviewMatches(review, serverStudioPackets(s, id(20), "role_aligned")[0], "context"), false);
});
test("malformed, foreign, capped and unproven Ready responses fail closed", () => {
  const edits = [s => { s.user_id = id(2); }, s => { s.ready = true; }, s => { s.ready = "true"; },
    s => { s.packets = Array(401).fill(s.packets[0]); }, s => { s.packets.push(s.packets[0]); },
    s => { s.packets[0].current = false; }, s => { s.packets[0].artifacts[0].resume_id = id(99); },
    s => { s.packets[0].artifacts[1].mime_type = mimes.docx; }, s => { s.packets[0].generated_at = "invalid"; },
    s => { s.packets[0].packet_fingerprint = "not a hash"; }, s => { s.pending_questions = 202; }];
  for (const edit of edits) { const s = durableFixture(); edit(s); assert.throws(() => checkedReadiness(s, id(1), id(10)), /invalid packet readiness/); }
});
test("Ready requires a matching current durable receipt not only a true flag", () => {
  for (const change of [s => { s.review = null; }, s => { s.review.current = false; }, s => { s.review.run_id = id(99); },
    s => { s.review.packet_fingerprint = "f".repeat(64); }, s => { s.pending_questions = 1; }, s => { s.application_status = "submitted"; }]) {
    const s = withReview(); change(s); assert.throws(() => checkedReadiness(s, id(1), id(10)));
  }
});
test("server stale projection wins over cached Ready even with the same manual timestamp", () => {
  const cached = { id: id(50), job_id: id(10), status: "ready", updated_at: "2026-09-15T12:05:00Z" };
  const fresh = { ...cached, status: "draft", recorded_status: "ready", readiness: { ...durableFixture(), reason: "review_stale" } };
  assert.equal(latestApplication([fresh], id(10), cached).status, "draft");
  assert.equal(latestApplication([{ ...fresh, readiness: undefined, readiness_unavailable: "capped_workspace" }], id(10), cached).status, "draft");
});
