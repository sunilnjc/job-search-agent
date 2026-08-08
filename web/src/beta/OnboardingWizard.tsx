import { useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Session } from "@supabase/supabase-js";
import { requireSupabase } from "./supabase";
import type { BetaProfile, JobPreferences } from "./types";

type OnboardingWizardProps = {
  session: Session;
  profile: BetaProfile | null;
  preferences: JobPreferences | null;
  onComplete: () => Promise<void>;
};

type Step = "basics" | "roles" | "eligibility" | "resume" | "review";

type WizardState = {
  displayName: string;
  baseLocation: string;
  targetTitles: string;
  preferredLocations: string;
  preferredRegions: string;
  remotePreference: JobPreferences["remote_preference"];
  sponsorshipRequired: boolean;
  workAuthorizationNotes: string;
  resumeFile: File | null;
};

const STEPS: Array<{ id: Step; label: string; title: string; description: string }> = [
  { id: "basics", label: "Basics", title: "Let’s start with the essentials.", description: "These details stay private to your account." },
  { id: "roles", label: "Target roles", title: "What work are you looking for?", description: "We use this to focus your shortlist, not to make assumptions about you." },
  { id: "eligibility", label: "Eligibility", title: "Set the practical boundaries.", description: "Clear eligibility saves time by avoiding roles that are not realistic." },
  { id: "resume", label: "Resume", title: "Add your approved source resume.", description: "It remains private and is never shared with an employer without your confirmation." },
  { id: "review", label: "Review", title: "Review your private workspace.", description: "You can change these preferences any time after setup." },
];

const EMPTY_PREFERENCES: Omit<JobPreferences, "user_id"> = {
  target_titles: [],
  preferred_locations: [],
  preferred_regions: [],
  remote_preference: "open",
  sponsorship_required: false,
  work_authorization_notes: null,
};

const ACCEPTED_EXTENSIONS = new Set(["pdf", "doc", "docx"]);

function listFrom(value: string) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function fileExtension(filename: string) {
  return filename.split(".").pop()?.toLowerCase() ?? "";
}

function sanitizedFilename(filename: string) {
  const extension = fileExtension(filename);
  const stem = filename.replace(/\.[^.]+$/, "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 120) || "resume";
  return extension ? `${stem}.${extension}` : stem;
}

function initialState(profile: BetaProfile | null, preferences: JobPreferences | null): WizardState {
  return {
    displayName: profile?.display_name ?? "",
    baseLocation: profile?.base_location ?? "",
    targetTitles: preferences?.target_titles.join(", ") ?? "",
    preferredLocations: preferences?.preferred_locations.join(", ") ?? "",
    preferredRegions: preferences?.preferred_regions.join(", ") ?? "",
    remotePreference: preferences?.remote_preference ?? "open",
    sponsorshipRequired: preferences?.sponsorship_required ?? false,
    workAuthorizationNotes: preferences?.work_authorization_notes ?? "",
    resumeFile: null,
  };
}

/**
 * A save-on-finish onboarding flow. Every database and Storage operation is
 * explicitly scoped to the active Supabase user; RLS remains the enforcement
 * layer and no privileged credential is used in the browser.
 */
export function OnboardingWizard({ session, profile, preferences, onComplete }: OnboardingWizardProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [form, setForm] = useState<WizardState>(() => initialState(profile, preferences));
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const step = STEPS[stepIndex];
  const isLastStep = stepIndex === STEPS.length - 1;

  const summary = useMemo(() => ({
    roles: listFrom(form.targetTitles),
    locations: listFrom(form.preferredLocations),
    regions: listFrom(form.preferredRegions),
  }), [form.preferredLocations, form.preferredRegions, form.targetTitles]);

  const update = <Key extends keyof WizardState>(key: Key, value: WizardState[Key]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const validateCurrentStep = () => {
    if (step.id === "basics" && !form.displayName.trim()) return "Add the name you want to use in your workspace.";
    if (step.id === "roles" && summary.roles.length === 0) return "Add at least one target role.";
    if (step.id === "resume" && form.resumeFile && !ACCEPTED_EXTENSIONS.has(fileExtension(form.resumeFile.name))) return "Upload a PDF, DOC, or DOCX resume.";
    return null;
  };

  const continueToNextStep = () => {
    const validationError = validateCurrentStep();
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    setStepIndex((current) => Math.min(current + 1, STEPS.length - 1));
  };

  const finish = async (event: FormEvent) => {
    event.preventDefault();
    const validationError = validateCurrentStep();
    if (validationError) {
      setError(validationError);
      return;
    }

    setIsSaving(true);
    setError(null);
    const client = requireSupabase();
    const userId = session.user.id;
    let uploadedPath: string | null = null;

    try {
      const now = new Date().toISOString();
      const [profileResult, preferencesResult] = await Promise.all([
        client.from("profiles").upsert({
          user_id: userId,
          display_name: form.displayName.trim(),
          base_location: form.baseLocation.trim() || null,
          onboarding_completed_at: now,
        }),
        client.from("job_preferences").upsert({
          user_id: userId,
          ...EMPTY_PREFERENCES,
          target_titles: summary.roles,
          preferred_locations: summary.locations,
          preferred_regions: summary.regions,
          remote_preference: form.remotePreference,
          sponsorship_required: form.sponsorshipRequired,
          work_authorization_notes: form.workAuthorizationNotes.trim() || null,
        }),
      ]);
      if (profileResult.error) throw profileResult.error;
      if (preferencesResult.error) throw preferencesResult.error;

      if (form.resumeFile) {
        const storagePath = `${userId}/${crypto.randomUUID()}-${sanitizedFilename(form.resumeFile.name)}`;
        const { error: uploadError } = await client.storage.from("resumes").upload(storagePath, form.resumeFile, {
          contentType: form.resumeFile.type || undefined,
          upsert: false,
        });
        if (uploadError) throw uploadError;
        uploadedPath = storagePath;

        const { data: existingDefault, error: existingDefaultError } = await client
          .from("resumes")
          .select("id")
          .eq("user_id", userId)
          .eq("is_default", true)
          .maybeSingle();
        if (existingDefaultError) throw existingDefaultError;

        const { error: resumeError } = await client.from("resumes").insert({
          user_id: userId,
          label: "Base resume",
          storage_path: storagePath,
          original_filename: form.resumeFile.name,
          mime_type: form.resumeFile.type || null,
          byte_size: form.resumeFile.size,
          is_default: !existingDefault,
        });
        if (resumeError) throw resumeError;
      }

      await onComplete();
    } catch (saveError) {
      if (uploadedPath) await client.storage.from("resumes").remove([uploadedPath]);
      setError(saveError instanceof Error ? saveError.message : "We could not save your workspace. Please try again.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <main className="beta-onboarding" aria-labelledby="beta-onboarding-title">
      <header className="beta-onboarding-header">
        <p className="beta-eyebrow">THE JOB PURSUIT · PRIVATE BETA</p>
        <p className="beta-onboarding-step-count">Step {stepIndex + 1} of {STEPS.length}</p>
        <ol className="beta-onboarding-progress" aria-label="Onboarding progress">
          {STEPS.map((item, index) => <li key={item.id} className={index === stepIndex ? "is-current" : index < stepIndex ? "is-complete" : ""} aria-current={index === stepIndex ? "step" : undefined}><span>{index + 1}</span><span>{item.label}</span></li>)}
        </ol>
      </header>

      <form className="beta-onboarding-form" onSubmit={finish} noValidate>
        <section className="beta-onboarding-panel" aria-live="polite">
          <h1 id="beta-onboarding-title">{step.title}</h1>
          <p>{step.description}</p>

          {step.id === "basics" && <div className="beta-onboarding-fields">
            <label>Name <span aria-hidden="true">*</span><input autoComplete="name" required value={form.displayName} onChange={(event) => update("displayName", event.target.value)} placeholder="Your name" /></label>
            <label>Current city and country <span className="beta-onboarding-hint">Optional</span><input autoComplete="address-level2" value={form.baseLocation} onChange={(event) => update("baseLocation", event.target.value)} placeholder="Dubai, United Arab Emirates" /></label>
          </div>}

          {step.id === "roles" && <div className="beta-onboarding-fields">
            <label>Target roles <span aria-hidden="true">*</span><span className="beta-onboarding-hint">Separate roles with commas</span><input required value={form.targetTitles} onChange={(event) => update("targetTitles", event.target.value)} placeholder="Forward Deployed Engineer, Senior Backend Engineer" /></label>
            <label>Preferred countries or cities <span className="beta-onboarding-hint">Optional · comma-separated</span><input value={form.preferredLocations} onChange={(event) => update("preferredLocations", event.target.value)} placeholder="Berlin, Amsterdam, Dubai" /></label>
            <label>Preferred regions <span className="beta-onboarding-hint">Optional · comma-separated</span><input value={form.preferredRegions} onChange={(event) => update("preferredRegions", event.target.value)} placeholder="Europe, Worldwide remote" /></label>
          </div>}

          {step.id === "eligibility" && <div className="beta-onboarding-fields">
            <fieldset><legend>Work preference</legend><select value={form.remotePreference} onChange={(event) => update("remotePreference", event.target.value as JobPreferences["remote_preference"])}><option value="open">Open to remote, hybrid, or on-site</option><option value="remote_only">Remote only</option><option value="hybrid">Hybrid</option><option value="onsite">On-site</option></select></fieldset>
            <label className="beta-onboarding-checkbox"><input type="checkbox" checked={form.sponsorshipRequired} onChange={(event) => update("sponsorshipRequired", event.target.checked)} /> <span>I need visa sponsorship for on-site relocation.</span></label>
            <label>Work authorisation notes <span className="beta-onboarding-hint">Optional</span><textarea value={form.workAuthorizationNotes} onChange={(event) => update("workAuthorizationNotes", event.target.value)} placeholder="For example: based in the UAE; open to relocation with employer sponsorship." rows={4} /></label>
          </div>}

          {step.id === "resume" && <div className="beta-onboarding-fields">
            <input ref={fileInput} className="beta-onboarding-file-input" type="file" accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => update("resumeFile", event.target.files?.[0] ?? null)} />
            <button type="button" className="beta-onboarding-upload" onClick={() => fileInput.current?.click()}>Choose a private resume</button>
            <p className="beta-onboarding-file-status">{form.resumeFile ? `${form.resumeFile.name} selected` : "PDF, DOC, or DOCX. You can also upload it later from your profile."}</p>
            <p className="beta-onboarding-privacy-note">Your resume is stored in your private account folder. The app uses it only to prepare documents you review.</p>
          </div>}

          {step.id === "review" && <dl className="beta-onboarding-review">
            <div><dt>Name</dt><dd>{form.displayName}</dd></div>
            <div><dt>Location</dt><dd>{form.baseLocation || "Not provided"}</dd></div>
            <div><dt>Target roles</dt><dd>{summary.roles.join(", ")}</dd></div>
            <div><dt>Preferred places</dt><dd>{[...summary.locations, ...summary.regions].join(", ") || "Open"}</dd></div>
            <div><dt>Work preference</dt><dd>{form.remotePreference.replace(/_/g, " ")}</dd></div>
            <div><dt>Sponsorship</dt><dd>{form.sponsorshipRequired ? "Required for relocation" : "Not currently required"}</dd></div>
            <div><dt>Base resume</dt><dd>{form.resumeFile?.name || "Not uploaded yet"}</dd></div>
          </dl>}
        </section>

        {error && <p className="beta-onboarding-error" role="alert">{error}</p>}
        <footer className="beta-onboarding-actions">
          <button type="button" className="beta-onboarding-back" onClick={() => { setError(null); setStepIndex((current) => Math.max(0, current - 1)); }} disabled={stepIndex === 0 || isSaving}>Back</button>
          {isLastStep ? <button type="submit" className="beta-onboarding-finish" disabled={isSaving}>{isSaving ? "Creating your workspace…" : "Create my private workspace"}</button> : <button type="button" className="beta-onboarding-continue" onClick={continueToNextStep}>Continue</button>}
        </footer>
      </form>
    </main>
  );
}
