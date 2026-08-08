import { useEffect, useMemo, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { betaEnabled, requireSupabase } from "./supabase";
import type { BetaJob, BetaProfile, JobPreferences } from "./types";
import { DocumentsPanel } from "./DocumentsPanel";
import { ApplicationStudio } from "./ApplicationStudio";
import { JobDetailWorkspace } from "./JobDetailWorkspace";
import { OnboardingWizard } from "./OnboardingWizard";
import { ApplicationsWorkspace } from "./ApplicationsWorkspace";
import "./beta.css";

type View = "today" | "applications" | "profile";
type AuthState = "loading" | "signed_out" | "signed_in";

function humanizeEligibility(value: BetaJob["eligibility_status"]) {
  return value === "needs_review" ? "Needs review" : value[0].toUpperCase() + value.slice(1);
}

export default function BetaApp() {
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [session, setSession] = useState<Session | null>(null);
  const [view, setView] = useState<View>("today");
  const [profile, setProfile] = useState<BetaProfile | null>(null);
  const [preferences, setPreferences] = useState<JobPreferences | null>(null);
  const [jobs, setJobs] = useState<BetaJob[]>([]);
  const [studioJob, setStudioJob] = useState<BetaJob | null>(null);
  const [detailJob, setDetailJob] = useState<BetaJob | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadWorkspace = async (current: Session) => {
    const client = requireSupabase();
    const [profileResult, preferencesResult, jobsResult] = await Promise.all([
      client.from("profiles").select("*").eq("user_id", current.user.id).maybeSingle(),
      client.from("job_preferences").select("*").eq("user_id", current.user.id).maybeSingle(),
      client.from("jobs").select("*").order("updated_at", { ascending: false }).limit(50),
    ]);
    const firstError = profileResult.error || preferencesResult.error || jobsResult.error;
    if (firstError) throw firstError;
    setProfile(profileResult.data as BetaProfile | null);
    setPreferences(preferencesResult.data as JobPreferences | null);
    setJobs((jobsResult.data ?? []) as BetaJob[]);
  };

  useEffect(() => {
    if (!betaEnabled) return;
    const client = requireSupabase();
    client.auth.getSession().then(async ({ data, error: sessionError }) => {
      if (sessionError) setError(sessionError.message);
      setSession(data.session);
      setAuthState(data.session ? "signed_in" : "signed_out");
      if (data.session) {
        try {
          await loadWorkspace(data.session);
        } catch (loadError) {
          setError(loadError instanceof Error ? loadError.message : "Could not load your workspace.");
        }
      }
    });
    const { data: subscription } = client.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      setAuthState(nextSession ? "signed_in" : "signed_out");
      if (!nextSession) {
        setProfile(null);
        setPreferences(null);
        setJobs([]);
      }
    });
    return () => subscription.subscription.unsubscribe();
  }, []);

  const readyJobs = useMemo(() => jobs.filter((job) => job.status === "ready"), [jobs]);
  const needsReview = useMemo(() => jobs.filter((job) => job.eligibility_status === "needs_review"), [jobs]);

  if (!betaEnabled) return <BetaSetupNeeded />;
  if (authState === "loading") return <main className="beta-shell beta-center"><p>Checking your secure session…</p></main>;
  if (authState === "signed_out") return <MagicLinkSignIn onNotice={setNotice} onError={setError} notice={notice} error={error} />;
  if (!session) return null;
  if (!profile?.onboarding_completed_at || !preferences) {
    return <OnboardingWizard session={session} profile={profile} preferences={preferences} onComplete={async () => {
      await loadWorkspace(session);
      setNotice("Your workspace is ready.");
    }} />;
  }

  return (
    <main className="beta-shell">
      <header className="beta-header">
        <div><p className="beta-eyebrow">THE JOB PURSUIT · BETA</p><h1>Good to see you{profile.display_name ? `, ${profile.display_name.split(" ")[0]}` : ""}.</h1></div>
        <button className="beta-text-button" onClick={async () => requireSupabase().auth.signOut()}>Sign out</button>
      </header>
      {notice && <p className="beta-notice" role="status">{notice}</p>}
      {error && <p className="beta-error" role="alert">{error}</p>}
      {view === "today" && <Today readyJobs={readyJobs} needsReview={needsReview} allJobs={jobs} userId={session.user.id} onViewApplications={() => setView("applications")} onOpenStudio={setStudioJob} onOpenDetail={setDetailJob} onJobsChanged={async () => {
        try { await loadWorkspace(session); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Could not refresh your jobs."); }
      }} />}
      {view === "applications" && <Applications jobs={jobs} onOpenStudio={setStudioJob} onOpenDetail={setDetailJob} />}
      {view === "profile" && <ProfileSummary session={session} profile={profile} preferences={preferences} />}
      <nav className="beta-navigation" aria-label="Primary navigation">
        {(["today", "applications", "profile"] as View[]).map((item) => <button key={item} className={view === item ? "active" : ""} onClick={() => setView(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}
      </nav>
      {studioJob && <div className="beta-studio-overlay" role="dialog" aria-modal="true" aria-label={`Application studio for ${studioJob.title}`}><div className="beta-studio-modal"><button className="beta-text-button beta-studio-close" onClick={() => setStudioJob(null)}>Close</button><ApplicationStudio session={session} job={studioJob} onApplicationStatusChange={async () => {
        try { await loadWorkspace(session); } catch (loadError) { setError(loadError instanceof Error ? loadError.message : "Could not refresh the application."); }
      }} /></div></div>}
      {detailJob && <JobDetailWorkspace job={detailJob} onClose={() => setDetailJob(null)} onOpenStudio={(job) => { setDetailJob(null); setStudioJob(job); }} />}
    </main>
  );
}

function BetaSetupNeeded() {
  return <main className="beta-shell beta-center"><p className="beta-eyebrow">THE JOB PURSUIT · BETA</p><h1>Private beta setup is in progress.</h1><p>This environment is intentionally separate from the founder’s personal dashboard. Configure the public Supabase URL and anonymous key to enable it.</p></main>;
}

function MagicLinkSignIn({ onNotice, onError, notice, error }: { onNotice: (value: string | null) => void; onError: (value: string | null) => void; notice: string | null; error: string | null }) {
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const send = async (event: React.FormEvent) => {
    event.preventDefault(); setSending(true); onError(null); onNotice(null);
    // The founder dashboard intentionally lives at `/`; beta sign-in must come
    // back to its own route after the magic-link callback.
    const { error: signInError } = await requireSupabase().auth.signInWithOtp({
      email,
      options: { emailRedirectTo: new URL("/beta", window.location.origin).toString() },
    });
    setSending(false);
    if (signInError) onError(signInError.message); else onNotice(`We sent a sign-in link to ${email}.`);
  };
  return <main className="beta-shell beta-auth"><p className="beta-eyebrow">THE JOB PURSUIT · PRIVATE BETA</p><h1>Find the right roles. Prepare stronger applications.</h1><p>Your profile and documents are private to your account.</p><form onSubmit={send}><label>Email address<input required type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label><button className="beta-primary" disabled={sending}>{sending ? "Sending link…" : "Continue with email"}</button></form>{notice && <p className="beta-notice" role="status">{notice}</p>}{error && <p className="beta-error" role="alert">{error}</p>}</main>;
}

function Today({ readyJobs, needsReview, allJobs, userId, onViewApplications, onOpenStudio, onOpenDetail, onJobsChanged }: { readyJobs: BetaJob[]; needsReview: BetaJob[]; allJobs: BetaJob[]; userId: string; onViewApplications: () => void; onOpenStudio: (job: BetaJob) => void; onOpenDetail: (job: BetaJob) => void; onJobsChanged: () => Promise<void> }) {
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"all" | "ready" | "review">("all");
  const matchesQuery = (job: BetaJob) => `${job.title} ${job.company_name} ${job.location_text ?? ""}`.toLowerCase().includes(query.trim().toLowerCase());
  const scopedJobs = allJobs.filter((job) => matchesQuery(job) && (scope === "all" || (scope === "ready" ? job.status === "ready" : job.eligibility_status === "needs_review")));
  const scopedReady = scopedJobs.filter((job) => job.status === "ready");
  const scopedReview = scopedJobs.filter((job) => job.eligibility_status === "needs_review");
  const scopedSaved = scopedJobs.filter((job) => job.status !== "ready");
  return <section className="beta-content"><div className="beta-today-heading"><div><p className="beta-subtitle">A focused, explainable shortlist—not a feed of thousands.</p></div><button className="beta-secondary" onClick={() => setAdding((value) => !value)}>{adding ? "Close" : "Add a job"}</button></div>{adding && <AddJobForm userId={userId} onSaved={async () => { setAdding(false); await onJobsChanged(); }} /> }<div className="beta-stats"><article><strong>{readyJobs.length}</strong><span>Ready to review</span></article><article><strong>{needsReview.length}</strong><span>Need your input</span></article><article><strong>{allJobs.length}</strong><span>In your workspace</span></article></div><div className="beta-role-search"><label><span className="beta-visually-hidden">Search saved roles</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search role, company, or location" /></label><div className="beta-role-search__filters" aria-label="Role list filter"><button className={scope === "all" ? "active" : ""} onClick={() => setScope("all")}>All</button><button className={scope === "ready" ? "active" : ""} onClick={() => setScope("ready")}>Ready</button><button className={scope === "review" ? "active" : ""} onClick={() => setScope("review")}>Needs review</button></div></div>{scopedReview.length > 0 && <JobSection title="Action required" jobs={scopedReview} empty="No eligibility questions are blocking you." onOpenStudio={onOpenStudio} onOpenDetail={onOpenDetail} />}{scopedReady.length > 0 ? <JobSection title="Ready to review" jobs={scopedReady} empty="" onOpenStudio={onOpenStudio} onOpenDetail={onOpenDetail} /> : <div className="beta-empty"><h2>{query || scope !== "all" ? "No roles match this view" : "No ready roles yet"}</h2><p>{query || scope !== "all" ? "Try a different search or return to all roles." : "When a source has been validated and matches your preferences, it will appear here. This keeps the list useful rather than noisy."}</p><button className="beta-secondary" onClick={() => { if (query || scope !== "all") { setQuery(""); setScope("all"); } else { onViewApplications(); } }}>{query || scope !== "all" ? "Clear filters" : "View applications"}</button></div>}{scopedSaved.length > 0 && <JobSection title="Saved roles" jobs={scopedSaved} empty="" onOpenStudio={onOpenStudio} onOpenDetail={onOpenDetail} />}</section>;
}

function AddJobForm({ userId, onSaved }: { userId: string; onSaved: () => Promise<void> }) {
  const [company, setCompany] = useState("");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [location, setLocation] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setSaving(true); setError(null);
    const { error: insertError } = await requireSupabase().from("jobs").insert({ user_id: userId, source: "manual", source_url: url, company_name: company, title, location_text: location || null, workplace_type: "unknown", eligibility_status: "unknown", status: "new" });
    setSaving(false);
    if (insertError) { setError(insertError.message); return; }
    await onSaved();
  };
  return <form className="beta-add-job" onSubmit={save}><h2>Add a role to your workspace</h2><p>Use a direct employer posting whenever possible. We will add validation and ranking next.</p><div className="beta-form-grid"><label>Company<input required value={company} onChange={(event) => setCompany(event.target.value)} /></label><label>Role title<input required value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="wide">Original posting URL<input required type="url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://careers.company.com/..." /></label><label>Location <span>Optional</span><input value={location} onChange={(event) => setLocation(event.target.value)} /></label></div><button className="beta-primary" disabled={saving}>{saving ? "Saving…" : "Save role"}</button>{error && <p className="beta-error" role="alert">{error}</p>}</form>;
}

function Applications({ jobs, onOpenStudio, onOpenDetail }: { jobs: BetaJob[]; onOpenStudio: (job: BetaJob) => void; onOpenDetail: (job: BetaJob) => void }) { return <section className="beta-content"><ApplicationsWorkspace jobs={jobs} onOpenStudio={onOpenStudio} onOpenDetail={onOpenDetail} /></section>; }

function ProfileSummary({ session, profile, preferences }: { session: Session; profile: BetaProfile; preferences: JobPreferences }) { return <section className="beta-content"><h2>Profile</h2><dl className="beta-profile"><div><dt>Base location</dt><dd>{profile.base_location || "Not provided"}</dd></div><div><dt>Target roles</dt><dd>{preferences.target_titles.join(", ") || "Not provided"}</dd></div><div><dt>Regions</dt><dd>{preferences.preferred_regions.join(", ") || "Open"}</dd></div><div><dt>Sponsorship</dt><dd>{preferences.sponsorship_required ? "Required for relocation" : "Not currently required"}</dd></div></dl><DocumentsPanel session={session} /></section>; }

function JobSection({ title, jobs, empty, onOpenStudio, onOpenDetail }: { title: string; jobs: BetaJob[]; empty: string; onOpenStudio: (job: BetaJob) => void; onOpenDetail: (job: BetaJob) => void }) { return <section className="beta-section"><h2>{title}</h2>{jobs.length === 0 ? <p className="beta-empty-inline">{empty}</p> : <div className="beta-job-list">{jobs.map((job) => <article className="beta-job" key={job.id}><div><span className={`beta-badge ${job.eligibility_status}`}>{humanizeEligibility(job.eligibility_status)}</span><h3>{job.title}</h3><p>{job.company_name} · {job.location_text || job.workplace_type || "Location not listed"}</p>{job.last_validated_at && <small>Validated {new Date(job.last_validated_at).toLocaleDateString()}</small>}</div><div className="beta-job-actions"><button className="beta-secondary" onClick={() => onOpenDetail(job)}>View role</button><button className="beta-secondary" onClick={() => onOpenStudio(job)}>Application studio</button><a href={job.source_url} target="_blank" rel="noreferrer">Original ↗</a></div></article>)}</div>}</section>; }
