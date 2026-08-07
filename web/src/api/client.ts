import type { ChatMessage, Job, JobDetail, OutreachDraft, RunStatus, Status, StatusSummary } from "../types";

// The API is served by the same FastAPI origin as the UI.  A relative URL also
// works through Cloudflare Tunnel, where port 8842 intentionally is not public.
const API_BASE = "";

export function resumeDocxUrl(id: number): string {
  return `${API_BASE}/api/jobs/${id}/resume-docx`;
}

export function resumePdfUrl(id: number): string {
  return `${API_BASE}/api/jobs/${id}/resume-pdf`;
}

export function coverLetterPdfUrl(id: number): string {
  return `${API_BASE}/api/jobs/${id}/cover-letter-pdf`;
}

/**
 * iOS standalone PWAs do not reliably honour an anchor's `download` attribute for PDFs.
 * Sharing a fetched File opens the native sheet (Save to Files, Mail, ATS app, etc.); desktop
 * browsers retain the normal download fallback.
 */
export async function shareOrDownloadDocument(url: string, filename: string): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not download document (${response.status})`);
  const blob = await response.blob();
  const file = new File([blob], filename, { type: blob.type || "application/octet-stream" });

  if (navigator.canShare?.({ files: [file] })) {
    await navigator.share({ files: [file], title: filename });
    return;
  }

  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json();
}

export const api = {
  listJobs: () => request<Job[]>("/api/jobs"),
  getJob: (id: number) => request<JobDetail>(`/api/jobs/${id}`),
  updateStatus: (id: number, status: Status) =>
    request(`/api/jobs/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
  excludeJob: (id: number, reason: string) =>
    request(`/api/jobs/${id}/exclude`, { method: "PATCH", body: JSON.stringify({ reason }) }),
  unexcludeJob: (id: number) => request(`/api/jobs/${id}/unexclude`, { method: "PATCH" }),
  generateDraft: (id: number) => request(`/api/jobs/${id}/draft`, { method: "POST" }),
  generateGaps: (id: number) => request(`/api/jobs/${id}/gaps`, { method: "POST" }),
  validateJob: (id: number) => request<{ available: boolean | null; reason: string | null; final_url: string }>(`/api/jobs/${id}/validate`, { method: "POST" }),
  statusSummary: () => request<StatusSummary>("/api/status/summary"),
  triggerFetch: (url?: string) =>
    request<RunStatus>("/api/runs/fetch", { method: "POST", body: JSON.stringify({ url }) }),
  triggerMatch: (limit?: number) =>
    request<RunStatus>("/api/runs/match", { method: "POST", body: JSON.stringify({ limit }) }),
  triggerAutopilot: (limit?: number) =>
    request<RunStatus>("/api/runs/autopilot", { method: "POST", body: JSON.stringify({ limit }) }),
  triggerDirectApply: (id: number) => request<RunStatus>(`/api/runs/jobs/${id}/direct-apply`, { method: "POST" }),
  getRun: (runId: string) => request<RunStatus>(`/api/runs/${runId}`),
  chat: (id: number, messages: ChatMessage[]) =>
    request<{ reply: string; materials_updated: boolean }>(`/api/jobs/${id}/chat`, {
      method: "POST",
      body: JSON.stringify({ messages }),
    }),
  createOutreach: (id: number) => request<OutreachDraft>(`/api/jobs/${id}/outreach`, { method: "POST" }),
  getOutreach: (id: number) => request<OutreachDraft>(`/api/jobs/${id}/outreach`),
  sendOutreach: (id: number, recipient: string) =>
    request<OutreachDraft>(`/api/jobs/${id}/outreach/send`, {
      method: "POST",
      body: JSON.stringify({ recipient }),
    }),
};
