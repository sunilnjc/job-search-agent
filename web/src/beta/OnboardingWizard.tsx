import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { Session } from "@supabase/supabase-js";
import type { BetaProfile, BetaResume, JobPreferences } from "./types";
import { RecoveryPanel } from "./RecoveryPanel";
import { loadMobileWorkspace, mobileRequest, uploadMobileResume } from "./mobile";
import { MobileApiError, validateResume } from "./mobileTransport";
import { clearDraft, draftStorage, readDraft, saveDraft } from "./onboardingDraft";
import type { OnboardingDraft, WizardFields } from "./onboardingDraft";
import { countryName, remoteCountries } from "./countries";

type Props = { session: Session; profile: BetaProfile | null; preferences: JobPreferences | null; onComplete: () => Promise<void> };
const STEPS = ["Basics", "Target roles", "Eligibility", "Resume", "Review"];
const listFrom = (value: string) => value.split(",").map(item => item.trim()).filter(Boolean);
function initialFields(profile: BetaProfile | null, preferences: JobPreferences | null): WizardFields {
  return {
    displayName: profile?.display_name ?? "", baseLocation: profile?.base_location ?? "",
    targetTitles: preferences?.target_titles.join(", ") ?? "", preferredLocations: preferences?.preferred_locations.join(", ") ?? "",
    preferredRegions: preferences?.preferred_regions.join(", ") ?? "", remotePreference: preferences?.remote_preference ?? "open",
    sponsorshipRequired: preferences?.sponsorship_required ?? false, workAuthorizationNotes: preferences?.work_authorization_notes ?? "",
    remoteCountryPolicy: preferences?.discovery_rules?.remote_country_policy ?? "review",
    remoteCountryCodes: preferences?.discovery_rules?.remote_country_codes.join(", ") ?? "",
    sponsorshipPolicy: preferences?.discovery_rules?.sponsorship_policy ?? "review",
    careerText: profile?.career_text ?? "", factsConfirmed: false,
  };
}
export function OnboardingWizard({ session, profile, preferences, onComplete }: Props) {
  const userId = session.user.id;
  const [draft, setDraft] = useState<OnboardingDraft>(() => readDraft(draftStorage(), userId) ?? {
    version: 1, step: 0, fields: initialFields(profile, preferences), fileName: "", uploadedResumeId: null, uploadUncertain: false,
  });
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [durable, setDurable] = useState(true);
  const [notice, setNotice] = useState("");
  const [availableResumes, setAvailableResumes] = useState<BetaResume[]>([]);
  const [recoveryKey, setRecoveryKey] = useState(0);
  const request = useRef(new AbortController());
  const completed = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    request.current = new AbortController();
    return () => { request.current.abort(); };
  }, []);
  useEffect(() => { if (!completed.current) setDurable(saveDraft(draftStorage(), userId, draft)); }, [draft, userId]);
  const fields = draft.fields;
  const summary = useMemo(() => ({ roles: listFrom(fields.targetTitles), locations: listFrom(fields.preferredLocations), regions: listFrom(fields.preferredRegions) }), [fields]);
  const update = <K extends keyof WizardFields>(key: K, value: WizardFields[K]) => setDraft(current => ({ ...current, fields: { ...current.fields, [key]: value, ...(key === "careerText" ? { factsConfirmed: false } : {}) } }));
  const validate = (all = false) => {
    if ((all || draft.step === 0) && !fields.displayName.trim()) return "Add your name in Basics.";
    if ((all || draft.step === 0) && fields.careerText.trim() && !fields.factsConfirmed) return "Review and confirm your career facts before continuing.";
    if (all || draft.step === 1) {
      if (!summary.roles.length) return "Add at least one target role.";
      if ([summary.roles, summary.locations, summary.regions].some(items => items.length > 30 || items.some(item => item.length > 160))) return "Use up to 30 items per list, each up to 160 characters.";
    }
    if (all || draft.step === 3) {
      if (draft.uploadUncertain) return "Check the previous upload before continuing.";
      if (draft.fileName && !draft.uploadedResumeId && !file) return "Reselect your resume in Resume, or explicitly skip the pending upload.";
      if (file) { try { validateResume(file); } catch (cause) { return (cause as Error).message; } }
    }
    if (all || draft.step === 2) {
      const codes = listFrom(fields.remoteCountryCodes ?? "").map(code => code.toUpperCase());
      if (codes.length > 30 || codes.some(code => !/^[A-Z]{2}$/.test(code)) || new Set(codes).size !== codes.length) return "Use unique two-letter country codes, such as AE, GB or DE, up to 30 countries.";
      if (!fields.sponsorshipRequired && fields.sponsorshipPolicy === "require_explicit") return "To require a sponsorship offer, keep ‘I need visa sponsorship’ selected, or change the sponsorship rule to review unknowns.";
    }
    return null;
  };
  const finish = async (event: FormEvent) => {
    event.preventDefault();
    if (saving) return;
    const invalid = validate(draft.step === 4);
    if (invalid) { setError(invalid); return; }
    if (draft.step < 4) { setError(null); setDraft(current => ({ ...current, step: current.step + 1 })); return; }
    setSaving(true); setError(null);
    const signal = request.current.signal;
    try {
      // Never save a completion marker before all requested work succeeds.
      // Partial profile updates preserve native structured career facts.
      await mobileRequest(userId, "/profile", { method: "PUT", signal, body: { display_name: fields.displayName.trim(), base_location: fields.baseLocation.trim() || null, career_text: fields.careerText.trim() } });
      await mobileRequest(userId, "/preferences", { method: "PUT", signal, body: {
        target_titles: summary.roles, preferred_locations: summary.locations, preferred_regions: summary.regions,
        remote_preference: fields.remotePreference, sponsorship_required: fields.sponsorshipRequired,
        work_authorization_notes: fields.workAuthorizationNotes.trim() || null, minimum_match_score: preferences?.minimum_match_score ?? 7,
        discovery_rules: {
          remote_country_policy: fields.remoteCountryPolicy ?? preferences?.discovery_rules?.remote_country_policy ?? "review",
          remote_country_codes: listFrom(fields.remoteCountryCodes ?? preferences?.discovery_rules?.remote_country_codes.join(", ") ?? "").map(code => code.toUpperCase()),
          sponsorship_policy: fields.sponsorshipPolicy ?? preferences?.discovery_rules?.sponsorship_policy ?? "review",
        },
      } });
      let savedResumeId = draft.uploadedResumeId;
      if (file && !draft.uploadedResumeId) {
        // Persist the ambiguous state before sending; a reload must not silently resend.
        const pending = { ...draft, uploadUncertain: true, uploadKey: draft.uploadKey ?? crypto.randomUUID() };
        saveDraft(draftStorage(), userId, pending); setDraft(pending);
        try {
          const resume = await uploadMobileResume(userId, file, signal, pending.uploadKey);
          const receipt = { ...pending, uploadedResumeId: resume.id, uploadUncertain: false };
          savedResumeId = resume.id;
          saveDraft(draftStorage(), userId, receipt); setDraft(receipt); setFile(null);
        } catch (cause) {
          if (cause instanceof MobileApiError && cause.status >= 400 && cause.status < 500) {
            const rejected = { ...pending, uploadUncertain: false }; saveDraft(draftStorage(), userId, rejected); setDraft(rejected);
          }
          setRecoveryKey(current => current + 1);
          throw cause;
        }
      }
      if (savedResumeId && !fields.careerText.trim()) {
        const extracted = await mobileRequest<{ text: string }>(userId, `/resumes/${savedResumeId}/text`, { signal });
        if (extracted.text.trim()) {
          // Never promote parsed resume claims to confirmed profile facts without
          // review. Keep the successful upload receipt so confirmation cannot reupload.
          setDraft(current => ({ ...current, uploadedResumeId: savedResumeId, uploadUncertain: false,
            fields: { ...current.fields, careerText: extracted.text, factsConfirmed: false } }));
          setNotice("Your resume is saved. Review the extracted career facts below, confirm they are accurate, then finish to find roles. No AI has run.");
          return;
        }
      }
      await mobileRequest(userId, "/profile", { method: "PUT", signal, body: { onboarding_completed_at: new Date().toISOString() } });
      await onComplete();
      completed.current = true; clearDraft(draftStorage(), userId);
    } catch (cause) {
      if (!signal.aborted) setError(cause instanceof Error ? cause.message : "Your draft is retained. Check your connection and try again.");
    } finally { if (!signal.aborted) setSaving(false); }
  };
  const checkUpload = async () => {
    setSaving(true); setError(null);
    try {
      const workspace = await loadMobileWorkspace(userId, request.current.signal);
      const candidates = workspace.resumes.filter(resume => resume.original_filename === draft.fileName);
      setAvailableResumes(candidates);
      if (candidates.length) {
        setNotice("Saved resumes with this filename were found. A filename alone does not prove it is this upload. Choose a saved resume below, or check recovery; no upload was retried.");
      } else {
        setNotice("No saved resume with this filename was found in the returned list. Reselect it or skip; uploading again is your choice.");
        setDraft(current => ({ ...current, uploadUncertain: false }));
      }
    } catch (cause) { setError((cause as Error).message); } finally { setSaving(false); }
  };
  return <main className="beta-onboarding" aria-labelledby="beta-onboarding-title">
    <header className="beta-onboarding-header"><p className="beta-eyebrow">THE JOB PURSUIT · PRIVATE BETA</p><p>Step {draft.step + 1} of 5</p>
      <ol className="beta-onboarding-progress" aria-label="Onboarding progress">{STEPS.map((label, index) => <li key={label} className={index === draft.step ? "is-current" : index < draft.step ? "is-complete" : ""} aria-current={index === draft.step ? "step" : undefined}><span>{index + 1}</span><span>{label}</span></li>)}</ol>
    </header>
    <p className="beta-onboarding-hint">{durable ? "Your text draft stays in this browser tab until completion or sign-out. File contents are not saved in the draft." : "Browser draft storage is unavailable. Keep this tab open to retain your work."}</p>
    <form className="beta-onboarding-form" onSubmit={finish}>
      <fieldset disabled={saving} className="workflow-fieldset">
      <section className="beta-onboarding-panel">
        <h1 id="beta-onboarding-title">{STEPS[draft.step]}</h1>
        {draft.step === 0 && <div className="beta-onboarding-fields">
          <label>Name<input autoComplete="name" required maxLength={160} value={fields.displayName} onChange={event => update("displayName", event.target.value)} /></label>
          <label>Current city and country<input maxLength={240} value={fields.baseLocation} onChange={event => update("baseLocation", event.target.value)} /></label>
          <label>Confirmed career facts <span>Optional now; needed for grounded preparation</span><textarea maxLength={100000} rows={8} value={fields.careerText} onChange={event => update("careerText", event.target.value)} placeholder="Your actual roles, dates, responsibilities, education and achievements. Do not add qualifications you do not hold." /></label>
          <label className="beta-onboarding-checkbox"><input type="checkbox" checked={fields.factsConfirmed} onChange={event => update("factsConfirmed", event.target.checked)} /><span>I reviewed these facts and confirm they accurately describe my career. These are self-reported, not independently verified.</span></label>
          <p>Structured qualifications saved on mobile are preserved. This editor changes only career text.</p>
        </div>}
        {draft.step === 1 && <div className="beta-onboarding-fields">
          <p>These preferences guide configured public-board discovery and role assessment. Coverage is limited to the operator’s selected boards, not the whole market. Manual posting links are not automatically fetched.</p>
          <label>Target roles <span>Comma-separated</span><input required value={fields.targetTitles} onChange={event => update("targetTitles", event.target.value)} /></label>
          <label>Preferred countries or cities<input value={fields.preferredLocations} onChange={event => update("preferredLocations", event.target.value)} /></label>
          <label>Preferred regions<input value={fields.preferredRegions} onChange={event => update("preferredRegions", event.target.value)} /></label>
        </div>}
        {draft.step === 2 && <div className="beta-onboarding-fields">
          <p>These preferences do not confirm work rights for any job. Review eligibility separately in each role’s Studio.</p>
          <label>Work preference<select value={fields.remotePreference} onChange={event => update("remotePreference", event.target.value as WizardFields["remotePreference"])}><option value="open">Open to remote, hybrid, or on-site</option><option value="remote_only">Remote only</option><option value="hybrid">Hybrid</option><option value="onsite">On-site</option></select></label>
          <label className="beta-onboarding-checkbox"><input type="checkbox" checked={fields.sponsorshipRequired} onChange={event => update("sponsorshipRequired", event.target.checked)} /><span>I need visa sponsorship for relocation.</span></label>
          <label>Remote location rule<select value={fields.remoteCountryPolicy ?? "review"} onChange={event => update("remoteCountryPolicy", event.target.value as WizardFields["remoteCountryPolicy"])}><option value="review">Include unclear remote locations for my review</option><option value="require_explicit">Require explicit worldwide or selected-country remote work</option></select></label>
          <label>Add a country for remote work<select value="" onChange={event => { if (event.target.value) update("remoteCountryCodes", [...new Set([...listFrom(fields.remoteCountryCodes ?? ""), event.target.value])].join(", ")); }}><option value="">Choose a country</option>{remoteCountries.map(country => <option key={country.code} value={country.code}>{country.name}</option>)}</select></label>
          <ul aria-label="Selected remote countries">{listFrom(fields.remoteCountryCodes ?? "").map(code => <li key={code}>{countryName(code)} <button type="button" aria-label={`Remove ${countryName(code)}`} onClick={() => update("remoteCountryCodes", listFrom(fields.remoteCountryCodes ?? "").filter(value => value !== code).join(", "))}>Remove</button></li>)}</ul>
          <p>Strict remote mode excludes unknown or conflicting country restrictions. With no countries selected, only explicit worldwide remote postings qualify. It does not establish work authorization.</p>
          <label>Sponsorship rule<select value={fields.sponsorshipPolicy ?? "review"} onChange={event => update("sponsorshipPolicy", event.target.value as WizardFields["sponsorshipPolicy"])}><option value="review">Include unclear sponsorship for my review</option><option value="require_explicit">Only postings explicitly offering sponsorship</option></select></label>
          <p>These are search rules, not employer guarantees. Salary, travel, notice period and company exclusions are not yet automated filters.</p>
          <label>Work authorisation notes<textarea maxLength={4000} value={fields.workAuthorizationNotes} onChange={event => update("workAuthorizationNotes", event.target.value)} /></label>
        </div>}
        {draft.step === 3 && <div className="beta-onboarding-fields">
          <p>Optional PDF or DOCX, up to 8 MiB. The server validates the file before saving it. No AI starts on upload.</p>
          <input aria-label="Onboarding resume" ref={input} type="file" accept=".pdf,.docx" onChange={event => {
            const selected = event.target.files?.[0]; if (!selected) return;
            try { validateResume(selected); setError(null); setFile(selected); setDraft(current => ({ ...current, fileName: selected.name, uploadedResumeId: null, uploadUncertain: false, uploadKey: current.fileName === selected.name ? current.uploadKey ?? crypto.randomUUID() : crypto.randomUUID() })); }
            catch (cause) { setError((cause as Error).message); event.target.value = ""; }
          }} disabled={draft.uploadUncertain} />
          <p>{draft.uploadedResumeId ? "Resume saved: " + draft.fileName : draft.fileName ? draft.fileName + (file ? " selected" : " — reselect the file to upload") : "No new resume selected. Existing resumes are kept."}</p>
          {draft.uploadUncertain && <button type="button" onClick={() => void checkUpload()}>Check previous upload</button>}
          <button type="button" onClick={() => { setFile(null); if (input.current) input.current.value = ""; setDraft(current => ({ ...current, fileName: "", uploadedResumeId: null, uploadUncertain: false, uploadKey: undefined })); setError(null); }}>Skip pending upload (keep any saved resume)</button>
        </div>}
        {draft.step === 4 && <div>
          <dl className="beta-onboarding-review"><div><dt>Name</dt><dd>{fields.displayName}</dd></div><div><dt>Target roles</dt><dd>{summary.roles.join(", ")}</dd></div><div><dt>Resume</dt><dd>{draft.uploadedResumeId ? "Already saved: " + draft.fileName : draft.fileName || "Skipped for now"}</dd></div><div><dt>Career facts</dt><dd>{fields.careerText.trim() ? fields.factsConfirmed ? "Reviewed self-reported text" : "Awaiting your review" : "Not added yet"}</dd></div></dl>
          <p>Next, Discover will find roles using your saved preferences and reviewed career facts. If you upload a resume without career facts, you will review its extracted text here first. Searching uses no AI credits; nothing is sent to an employer.</p>
          {fields.careerText.trim() && draft.uploadedResumeId && <div className="beta-onboarding-fields"><h3>Review your resume facts</h3><p>Check employers, dates, skills and qualifications. Correct extraction mistakes; nothing here is independently verified.</p><label>Review extracted career facts<textarea rows={10} maxLength={100000} value={fields.careerText} onChange={event => update("careerText", event.target.value)} /></label><label className="beta-onboarding-checkbox"><input type="checkbox" checked={fields.factsConfirmed} onChange={event => update("factsConfirmed", event.target.checked)} /><span>I reviewed these extracted facts and confirm they are accurate.</span></label></div>}
          {draft.uploadUncertain && <button type="button" onClick={() => void checkUpload()}>Check previous upload</button>}
        </div>}
      </section>
      <footer className="beta-onboarding-actions">
        <button type="button" className="beta-onboarding-back" disabled={draft.step === 0} onClick={() => { setError(null); setDraft(current => ({ ...current, step: current.step - 1 })); }}>Back</button>
        <button type="submit" className="beta-onboarding-finish">{saving ? "Saving…" : draft.step < 4 ? "Continue" : profile?.onboarding_completed_at ? "Save changes" : "Create my private workspace"}</button>
      </footer>
      </fieldset>
      {error && <p className="beta-onboarding-error" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
    </form>
    {(draft.step === 3 || draft.step === 4) && <div className="workflow-stack workflow">
      {availableResumes.map(resume => <button disabled={saving} key={resume.id} onClick={() => { setDraft(current => ({ ...current, uploadedResumeId: resume.id, fileName: resume.original_filename, uploadUncertain: false })); setFile(null); setAvailableResumes([]); setError(null); }}>Use saved resume: {resume.original_filename} ({resume.id})</button>)}
      <RecoveryPanel userId={userId} kind="resume" refreshKey={recoveryKey} onRecovered={async result => {
        if (!("deleted" in result)) { setDraft(current => ({ ...current, uploadedResumeId: result.id, fileName: result.original_filename, uploadUncertain: false })); setFile(null); setError(null); }
      }} />
    </div>}
  </main>;
}
