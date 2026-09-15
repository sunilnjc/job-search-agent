import assert from "node:assert/strict";
import { test } from "node:test";
import { latestApplication } from "../src/beta/studioReadiness.ts";
import { groupRoles, applicationCounts } from "../src/beta/workspace.ts";

const cached = { id: "app", job_id: "role", status: "ready", updated_at: "2026-09-15T12:00:00Z" };
const unavailable = { ...cached, status: "draft", recorded_status: "ready", readiness_unavailable: "packet_check_failed" };

test("bootstrap unavailable projection beats cached Ready without timestamp changes", () => {
  assert.equal(latestApplication([unavailable], "role", cached), unavailable);
  assert.equal(latestApplication([unavailable], "role", cached).status, "draft");
});

test("only successfully verified roles appear in Ready; unavailable role stays accessible", () => {
  const failed = { id: "role", status: "matched", eligibility_status: "eligible", readiness_unavailable: "packet_check_failed" };
  const healthy = { id: "healthy", status: "ready", eligibility_status: "eligible" };
  const unrelated = { id: "unrelated", status: "new", eligibility_status: "unknown" };
  const groups = groupRoles([failed, healthy, unrelated]);
  assert.deepEqual(groups.ready.map(row => row.id), ["healthy"]);
  assert.deepEqual(groups.saved.map(row => row.id), ["role", "unrelated"]);
});

test("explicit refresh uses recovered server projection without retaining stale warning", () => {
  const recovered = { ...cached, readiness: { version: "packet-v1", ready: true } };
  assert.equal(latestApplication([recovered], "role", unavailable), recovered);
  assert.equal(latestApplication([recovered], "role", unavailable).readiness_unavailable, undefined);
});

test("unknown eligibility recovery cannot resurrect cached Ready or alter external history", () => {
  const unknown = { ...cached, status: "draft", recorded_status: "ready", readiness: { version: "packet-v1", ready: false, reason: "eligibility_required" } };
  assert.equal(latestApplication([unknown], "role", cached).status, "draft");
  const submitted = { id: "external", job_id: "external-role", status: "submitted", applied_at: "2026-09-14T12:00:00Z" };
  assert.equal(latestApplication([unavailable, submitted], "external-role", null), submitted);
  assert.equal(applicationCounts([unavailable, submitted]).submitted, 1);
});
