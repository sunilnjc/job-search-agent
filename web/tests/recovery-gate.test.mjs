import assert from "node:assert/strict";
import { test } from "node:test";
import { allMissingBytes, checkedRecoveryOperations, missingArtifactProof, newPreparationAcknowledged, operationSet, recoveryClear, recoveryScope } from "../src/beta/recoveryGate.ts";
const id = "00000000-0000-4000-8000-000000000001";
const second = "00000000-0000-4000-8000-000000000002";
const row = { id, state: "upload_pending", filename: "old-document.pdf", job_id: "job-one" };
const scope = recoveryScope("owner-one", "artifact", "job-one");
const snapshot = { scope, phase: "checked", operations: [row], missingBytes: [id] };
const approved = (state = snapshot, selected = "fresh", explicit = "fresh", live = ["fresh"], acknowledgements = [id]) => newPreparationAcknowledged(state, scope, selected, explicit, live, acknowledgements);

test("recovery is fail closed while checking, on errors, and for different owner/job scopes", () => {
  for (const phase of ["checking", "error"]) {
    const state = { ...snapshot, phase, operations: [] };
    assert.equal(recoveryClear(state, scope), false); assert.equal(approved(state), false);
  }
  for (const changedScope of [recoveryScope("owner-two", "artifact", "job-one"), recoveryScope("owner-one", "artifact", "job-two")]) {
    assert.equal(recoveryClear({ ...snapshot, scope: changedScope, operations: [] }, scope), false);
    assert.equal(approved({ ...snapshot, scope: changedScope }), false);
  }
});
test("only verified empty records clear the ordinary recovery gate", () => {
  assert.equal(recoveryClear({ ...snapshot, operations: [] }, scope), true);
  assert.equal(recoveryClear(snapshot, scope), false);
  assert.equal(allMissingBytes({ ...snapshot, operations: [] }, scope), false);
});
test("missing bytes require exact 409 code and matching recovered operation, never an inferred error message", () => {
  assert.equal(missingArtifactProof({ status: 409, code: "artifact_upload_bytes_required", operationId: id }, id), true);
  for (const error of [{ status: 404, code: "artifact_upload_bytes_required", operationId: id }, { status: 503, code: "artifact_recovery_required", operationId: id }, { status: 409, code: "artifact_upload_bytes_required", operationId: second }, { status: 409, message: "bytes are missing" }]) assert.equal(missingArtifactProof(error, id), false);
});
test("fresh explicit live source selection AND per-operation acknowledgement enable a new preparation", () => {
  assert.equal(approved(), true);
  assert.equal(approved(snapshot, "fresh", null), false);
  assert.equal(approved(snapshot, "fresh", "old"), false);
  assert.equal(approved(snapshot, "fresh", "fresh", []), false);
  assert.equal(approved(snapshot, "fresh", "fresh", ["fresh"], []), false);
  assert.equal(approved({ ...snapshot, missingBytes: [] }), false);
});
test("every pending operation needs a missing-bytes proof and acknowledgement; new operations invalidate approval", () => {
  const expanded = { ...snapshot, operations: [row, { ...row, id: second }] };
  assert.equal(approved(expanded), false);
  assert.equal(approved({ ...expanded, missingBytes: [id, second] }), false);
  assert.equal(approved({ ...expanded, missingBytes: [id, second] }, "fresh", "fresh", ["fresh"], [id, second]), true);
  assert.notEqual(operationSet(snapshot.operations), operationSet(expanded.operations));
});
test("operation comparison is order-independent without hiding state changes", () => {
  const rows = [row, { ...row, id: second }];
  assert.equal(operationSet(rows), operationSet([...rows].reverse()));
  assert.notEqual(operationSet(rows), operationSet([{ ...row, state: "ready" }, rows[1]]));
});
test("malformed, capped, duplicate or unknown-state recovery lists cannot look verified empty", () => {
  for (const value of [null, {}, [null], [{ ...row, id: null }], [{ ...row, state: "unknown" }], [row, row], Array(200).fill(row)]) assert.throws(() => checkedRecoveryOperations(value, "artifact", "job-one"));
  assert.deepEqual(checkedRecoveryOperations([], "artifact", "job-one"), []);
  assert.deepEqual(checkedRecoveryOperations([row], "artifact", "job-two"), []);
});
test("new preparation acknowledgement does not mutate or resolve the old operation journal", () => {
  const frozen = structuredClone(snapshot);
  assert.equal(approved(frozen), true);
  assert.deepEqual(frozen, snapshot);
  assert.equal(recoveryClear(frozen, scope), false);
});
