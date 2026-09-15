export type RecoveryOperation = { id: string; state: string; filename: string; job_id?: string };
export type RecoverySnapshot = { scope: string; phase: "checking" | "checked" | "error"; operations: RecoveryOperation[]; missingBytes: string[] };
export const recoveryScope = (userId: string, kind: "resume" | "artifact", jobId?: string) => `${userId}:${kind}:${jobId ?? "all"}`;
export const operationSet = (operations: RecoveryOperation[]) => operations.map(row => `${row.id}:${row.state}`).sort().join("|");
export function checkedRecoveryOperations(value: unknown, kind: "resume" | "artifact", jobId?: string): RecoveryOperation[] {
  // A full capped page cannot establish that all outstanding operations are known.
  if (!Array.isArray(value) || value.length >= 200 || value.some(row => !row || typeof row !== "object"
    || typeof row.id !== "string" || !/^[a-f0-9-]{36}$/i.test(row.id)
    || typeof row.filename !== "string" || !row.filename || typeof row.state !== "string"
    || (kind === "artifact" && (row.state !== "upload_pending" || typeof row.job_id !== "string"))
    || (kind === "resume" && !["upload_pending", "delete_pending"].includes(row.state)))) throw new Error("Recovery records could not be verified completely. AI remains blocked. Refresh recovery or contact the service operator.");
  if (new Set(value.map(row => row.id)).size !== value.length) throw new Error("Recovery records contain duplicate identities. Refresh recovery before continuing.");
  return value.filter(row => !jobId || row.job_id === jobId);
}
export function allMissingBytes(snapshot: RecoverySnapshot, scope: string) {
  return snapshot.scope === scope && snapshot.phase === "checked" && snapshot.operations.length > 0
    && snapshot.operations.every(row => snapshot.missingBytes.includes(row.id));
}
export function recoveryClear(snapshot: RecoverySnapshot, scope: string) {
  return snapshot.scope === scope && snapshot.phase === "checked" && snapshot.operations.length === 0;
}
export function newPreparationAcknowledged(snapshot: RecoverySnapshot, scope: string, selectedResumeId: string, explicitlySelectedResumeId: string | null, liveResumeIds: string[], acknowledgedOperations: string[]) {
  return allMissingBytes(snapshot, scope) && !!selectedResumeId && explicitlySelectedResumeId === selectedResumeId
    && liveResumeIds.includes(selectedResumeId) && snapshot.operations.every(row => acknowledgedOperations.includes(row.id));
}
export function missingArtifactProof(error: { status?: number; code?: string; operationId?: string }, operationId: string) {
  return error.status === 409 && error.code === "artifact_upload_bytes_required" && error.operationId === operationId;
}
