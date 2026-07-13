import type { ChatMessage, Job, JobDetail, RunStatus, Status, StatusSummary } from "../types";

// Works whether the page is opened via localhost or the Mac's LAN IP from a phone.
const API_BASE = `${window.location.protocol}//${window.location.hostname}:8842`;

export function resumeDocxUrl(id: number): string {
  return `${API_BASE}/api/jobs/${id}/resume-docx`;
}

export function resumePdfUrl(id: number): string {
  return `${API_BASE}/api/jobs/${id}/resume-pdf`;
}

export function coverLetterPdfUrl(id: number): string {
  return `${API_BASE}/api/jobs/${id}/cover-letter-pdf`;
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
  statusSummary: () => request<StatusSummary>("/api/status/summary"),
  triggerFetch: (url?: string) =>
    request<RunStatus>("/api/runs/fetch", { method: "POST", body: JSON.stringify({ url }) }),
  triggerMatch: (limit?: number) =>
    request<RunStatus>("/api/runs/match", { method: "POST", body: JSON.stringify({ limit }) }),
  getRun: (runId: string) => request<RunStatus>(`/api/runs/${runId}`),
  chat: (id: number, messages: ChatMessage[]) =>
    request<{ reply: string }>(`/api/jobs/${id}/chat`, {
      method: "POST",
      body: JSON.stringify({ messages }),
    }),
};
