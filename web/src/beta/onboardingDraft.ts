export type WizardFields = {
  displayName: string; baseLocation: string; targetTitles: string;
  preferredLocations: string; preferredRegions: string;
  remotePreference: "remote_only" | "hybrid" | "onsite" | "open";
  sponsorshipRequired: boolean; workAuthorizationNotes: string;
  remoteCountryPolicy?: "review" | "require_explicit";
  remoteCountryCodes?: string;
  sponsorshipPolicy?: "review" | "require_explicit";
  careerText: string; factsConfirmed: boolean;
};
export type OnboardingDraft = {
  version: 1; step: number; fields: WizardFields; fileName: string;
  uploadedResumeId: string | null; uploadUncertain: boolean;
  uploadKey?: string;
};
const prefix = "job-pursuit:onboarding:v1:";
/** Accessing window.sessionStorage itself can throw in privacy-restricted contexts. */
export function draftStorage(): Pick<Storage, "getItem" | "setItem" | "removeItem"> {
  try { return window.sessionStorage; }
  catch { return { getItem: () => null, setItem: () => { throw new Error("Draft storage unavailable"); }, removeItem: () => {} }; }
}
export function readDraft(storage: Pick<Storage, "getItem">, userId: string): OnboardingDraft | null {
  try {
    const raw = storage.getItem(prefix + userId);
    if (!raw || raw.length > 150000) return null;
    const draft = JSON.parse(raw) as OnboardingDraft;
    if (draft.version !== 1 || !Number.isInteger(draft.step) || draft.step < 0 || draft.step > 4 || !draft.fields) return null;
    for (const key of ["displayName", "baseLocation", "targetTitles", "preferredLocations", "preferredRegions", "workAuthorizationNotes", "careerText"] as const) if (typeof draft.fields[key] !== "string") return null;
    if (!["open", "remote_only", "hybrid", "onsite"].includes(draft.fields.remotePreference) || typeof draft.fields.sponsorshipRequired !== "boolean" || typeof draft.fields.factsConfirmed !== "boolean" || typeof draft.fileName !== "string" || typeof draft.uploadUncertain !== "boolean" || !(draft.uploadedResumeId === null || typeof draft.uploadedResumeId === "string")) return null;
    for (const key of ["remoteCountryPolicy", "sponsorshipPolicy"] as const) if (draft.fields[key] !== undefined && !["review", "require_explicit"].includes(draft.fields[key]!)) return null;
    if (draft.fields.remoteCountryCodes !== undefined && (typeof draft.fields.remoteCountryCodes !== "string" || draft.fields.remoteCountryCodes.length > 160)) return null;
    return draft;
  } catch { return null; }
}
export function saveDraft(storage: Pick<Storage, "setItem">, userId: string, draft: OnboardingDraft): boolean {
  try { storage.setItem(prefix + userId, JSON.stringify(draft)); return true; } catch { return false; }
}
export function clearDraft(storage: Pick<Storage, "removeItem">, userId: string) {
  try { storage.removeItem(prefix + userId); } catch { /* Storage may be disabled. */ }
}
