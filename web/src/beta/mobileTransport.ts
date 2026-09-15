import type { BetaQuestion } from "./types.ts";

type AuthSession = { access_token: string; user: { id: string } } | null;
type Options = { method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"; body?: unknown; signal?: AbortSignal; timeoutMs?: number; blob?: boolean; download?: boolean; idempotencyKey?: string };
export type MobileDownload = { blob: Blob; filename: string | null };
export function downloadFilename(disposition: string | null): string | null {
  if (!disposition) return null;
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = disposition.match(/filename="([^"]+)"/i)?.[1] ?? disposition.match(/filename=([^;]+)/i)?.[1];
  try {
    const name = (encoded ? decodeURIComponent(encoded) : plain)?.trim();
    return name && name.length <= 180 && name !== "." && name !== ".." && !Array.from(name).some(char => char === "/" || char === "\\" || char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) ? name : null;
  } catch { return null; }
}
export class MobileApiError extends Error {
  status: number;
  questions: BetaQuestion[];
  uncertain: boolean;
  code?: string;
  operationId?: string;
  discoveryResult?: unknown;
  retryAfter?: number;
  constructor(message: string, status = 0, questions: BetaQuestion[] = [], uncertain = false, detail?: { code?: string; operation_id?: string }) {
    super(message); this.name = "MobileApiError"; this.status = status; this.questions = questions; this.uncertain = uncertain;
    this.code = detail?.code; this.operationId = detail?.operation_id;
  }
}

/** Fixed same-origin tenant API. Never retries writes or falls back to founder routes. */
export function createMobileTransport(getSession: () => Promise<AuthSession>, fetcher: typeof fetch = fetch) {
  return async function request<T>(userId: string, path: string, options: Options = {}): Promise<T> {
    if (!/^\/(?:bootstrap|profile|preferences|resumes|artifacts|jobs|applications|questions|resume-operations|artifact-operations|account|discovery|billing)(?:\/[a-zA-Z0-9-]+)*$/.test(path)) {
      throw new MobileApiError("Invalid private workspace endpoint.");
    }
    if (options.idempotencyKey && !/^[a-f0-9-]{36}$/i.test(options.idempotencyKey)) throw new MobileApiError("Invalid upload operation key.");
    const session = await getSession();
    if (!session?.access_token || session.user.id !== userId) throw new MobileApiError("Your session changed. Sign in again before continuing.", 401);
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) controller.abort();
    const timer = setTimeout(abort, options.timeoutMs ?? 30000);
    const method = options.method ?? "GET";
    const readOnly = method === "GET" || (path === "/discovery/search" && method === "POST");
    try {
      const response = await fetcher(`/api/mobile${path}`, {
        method, headers: { Authorization: `Bearer ${session.access_token}`, ...(options.body === undefined ? {} : { "Content-Type": "application/json" }), ...(options.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}) },
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: "omit", redirect: "error", cache: "no-store", signal: controller.signal,
      });
      const current = await getSession();
      if (!current || current.user.id !== userId || controller.signal.aborted) throw new MobileApiError("The request was interrupted. Refresh before retrying any change.", 0, [], !readOnly);
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = payload.detail;
        const questions = Array.isArray(detail?.questions) ? detail.questions : [];
        const guidance = typeof detail === "string" ? detail.slice(0, 1000)
          : typeof detail?.message === "string" ? detail.message.slice(0, 1000) : undefined;
        const message = response.status === 401 ? "Your session expired. Sign in again."
          : response.status === 403 ? guidance || "This account cannot access this private beta workspace."
          : response.status === 429 ? guidance || "The workspace request limit was reached. Wait before trying again."
          : guidance ? guidance
          : response.status === 422 ? "Check the form values and file format before trying again."
          : "The private workspace service could not complete this request.";
        const failure = new MobileApiError(message, response.status, questions, false, detail && typeof detail === "object" ? detail : undefined);
        const retryAfter = Number(response.headers.get("Retry-After"));
        if (Number.isFinite(retryAfter) && retryAfter > 0) failure.retryAfter = Math.min(3600, Math.ceil(retryAfter));
        if (path === "/discovery/search") failure.discoveryResult = payload.status === "unavailable" ? payload : detail?.status === "unavailable" ? detail : undefined;
        throw failure;
      }
      const result = options.download ? { blob: await response.blob(), filename: downloadFilename(response.headers.get("Content-Disposition")) }
        : options.blob ? await response.blob() : await response.json();
      // A download/body read may outlive an account switch. Do not expose that body.
      const finalSession = await getSession();
      if (controller.signal.aborted || finalSession?.user.id !== userId) throw new MobileApiError("Your session changed before the response completed. Refresh before continuing.", 401);
      return result as T;
    } catch (error) {
      if (error instanceof MobileApiError) throw error;
      throw new MobileApiError(readOnly ? path === "/discovery/search" ? "Discovery search could not complete. No role was saved. Check your connection and search again when ready." : "Could not load the private workspace. Check your connection and refresh."
        : "The request was interrupted and may have completed. Refresh to check before retrying; no automatic retry was made.", 0, [], !readOnly);
    } finally {
      clearTimeout(timer); options.signal?.removeEventListener("abort", abort);
    }
  };
}

export const MAX_RESUME_BYTES = 8 * 1024 * 1024;
export function validateResume(file: Pick<File, "name" | "size">): void {
  if (!/\.(pdf|docx)$/i.test(file.name)) throw new Error("Choose a PDF or DOCX resume. Legacy DOC files are not supported.");
  if (file.name.length > 180 || Array.from(file.name).some(char => char === "/" || char === "\\" || char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) throw new Error("Use a filename up to 180 characters without path or control characters.");
  if (!file.size || file.size > MAX_RESUME_BYTES) throw new Error("The resume must be non-empty and no larger than 8 MiB.");
}
export async function resumePayload(file: File) {
  validateResume(file);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  return { filename: file.name, label: file.name.replace(/\.[^.]+$/, "").slice(0, 160) || "Source resume", content_base64: btoa(binary) };
}
