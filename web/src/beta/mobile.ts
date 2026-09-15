import { requireSupabase } from "./supabase";
import { createMobileTransport, resumePayload } from "./mobileTransport";
import type { MobileDownload } from "./mobileTransport";
import type { BetaResume, BetaWorkspace } from "./types";

export const mobileRequest = createMobileTransport(async () => {
  const { data, error } = await requireSupabase().auth.getSession();
  if (error) throw new Error("Could not restore your session. Sign in again.");
  return data.session;
});
export const loadMobileWorkspace = (userId: string, signal?: AbortSignal) => mobileRequest<BetaWorkspace>(userId, "/bootstrap", { signal });
export const uploadMobileResume = async (userId: string, file: File, signal?: AbortSignal, idempotencyKey?: string) => mobileRequest<BetaResume>(userId, "/resumes", { method: "POST", body: await resumePayload(file), signal, timeoutMs: 60000, idempotencyKey });
export async function downloadMobileFile(userId: string, kind: "resumes" | "artifacts", id: string, filename: string, signal?: AbortSignal) {
  const origin = document.activeElement;
  const blob = await mobileRequest<Blob>(userId, `/${kind}/${id}/download`, { blob: true, signal });
  if (signal?.aborted) return;
  savePrivateDownload(blob, filename, origin);
}
export async function downloadAccountExport(userId: string, requestId: string, signal?: AbortSignal) {
  const origin = document.activeElement;
  const result = await mobileRequest<MobileDownload>(userId, `/account/exports/${requestId}/download`, { download: true, signal });
  if (!signal?.aborted) savePrivateDownload(result.blob, result.filename ?? "job-pursuit-account-export", origin);
}
function savePrivateDownload(blob: Blob, filename: string, origin: Element | null) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = Array.from(filename).map(char => char === "/" || char === "\\" || char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 ? "_" : char).join("");
  anchor.click();
  // A disabled download button may drop focus to the page's skip link while the
  // browser handles the blob. Restore the invoker after React clears busy state.
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (origin instanceof HTMLElement && origin.isConnected && (document.activeElement === document.body || document.activeElement === anchor || document.activeElement?.classList.contains("pursuit-skip"))) origin.focus({ preventScroll: true });
  }));
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
