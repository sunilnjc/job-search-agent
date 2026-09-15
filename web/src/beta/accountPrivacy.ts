export type PrivacyRequest = {
  id: string; kind: "export" | "erase";
  state: "queued" | "processing" | "blocked" | "complete" | "failed";
  created_at: string; completed_at?: string | null;
  expires_at?: string | null;
  error_code?: string | null; message?: string | null; download_ready?: boolean;
};
export type AccountPrivacy = {
  requests: PrivacyRequest[];
  capability: { export: boolean; erasure: boolean; processing_configured: boolean };
};
export const ERASURE_CONFIRMATION = "DELETE MY ACCOUNT";
export function erasurePayload(confirmation: string, acknowledged: boolean, verifiedEmail: string | null) {
  if (!verifiedEmail?.trim()) throw new Error("A verified session email is required. Sign in again with your verified account.");
  if (confirmation !== ERASURE_CONFIRMATION || !acknowledged) throw new Error("Type DELETE MY ACCOUNT exactly and acknowledge the queued erasure request.");
  return { confirmation: ERASURE_CONFIRMATION, email: verifiedEmail };
}
export const pendingPrivacyRequest = (request: PrivacyRequest) => ["queued", "processing", "blocked"].includes(request.state);
export const canDownloadExport = (request: PrivacyRequest) => request.kind === "export" && request.state === "complete" && request.download_ready === true;
export function privacyStateGuidance(request: PrivacyRequest): string {
  if (request.state === "queued") return "Request recorded, awaiting processing. This does not mean the work is complete. Use Refresh requests to check.";
  if (request.state === "processing") return "The service reports processing is in progress. Refresh requests later; no automatic polling is running.";
  if (request.state === "blocked") return "Processing is blocked. Follow the service message or contact the service operator with this request ID. Refresh after the issue is resolved; do not submit duplicates.";
  if (request.state === "failed") return "The service reports this request failed. Read the message and contact the service operator with this request ID before deciding whether to submit another request.";
  return request.kind === "export" ? canDownloadExport(request) ? "The service reports this export is ready to download. Store it securely; it contains private account data." : "The service reports completion, but a download is not ready. Refresh or contact the service operator with this request ID."
    : "The service reports this erasure request complete. Check its message for scope and any data retained; this page does not independently verify deletion.";
}
export function checkedPrivacyRequest(value: unknown): PrivacyRequest {
  const item = value as PrivacyRequest | null;
  if (!item || typeof item.id !== "string" || !/^[a-zA-Z0-9-]+$/.test(item.id) || !["export", "erase"].includes(item.kind) || !["queued", "processing", "blocked", "complete", "failed"].includes(item.state) || typeof item.created_at !== "string"
    || (item.download_ready !== undefined && typeof item.download_ready !== "boolean")
    || [item.message, item.error_code, item.completed_at, item.expires_at].some(field => field != null && typeof field !== "string")) throw new Error("The service returned an unrecognized privacy request. Refresh requests before taking another action.");
  return item;
}
export function checkedAccountPrivacy(value: unknown): AccountPrivacy {
  const data = value as AccountPrivacy | null;
  if (!data || !Array.isArray(data.requests) || !data.capability || [data.capability.export, data.capability.erasure, data.capability.processing_configured].some(flag => typeof flag !== "boolean")) throw new Error("Privacy request status is unavailable. Refresh requests; workspace billing does not control this page.");
  return { ...data, requests: data.requests.map(checkedPrivacyRequest) };
}
