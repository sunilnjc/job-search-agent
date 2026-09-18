import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { betaEnabled, requireSupabase } from "./supabase";
import type { BetaApplication, BetaJob, BetaProfile, JobPreferences } from "./types";
import { DocumentsPanel } from "./DocumentsPanel";
import { ApplicationStudio } from "./ApplicationStudio";
import { JobDetailWorkspace } from "./JobDetailWorkspace";
import { OnboardingWizard } from "./OnboardingWizard";
import { ApplicationsWorkspace } from "./ApplicationsWorkspace";
import { BetaAuthLanding } from "./BetaAuthLanding";
import { BrandIdentity } from "./BrandIdentity";
import { RankHome } from "./RankHome";
import { WorkspaceIcon } from "./WorkspaceIcon";
import { WorkspaceDialog } from "./WorkspaceDialog";
import { activeRoles, groupRoles, safePostingUrl, WORKSPACE_TABS } from "./workspace";
import type { WorkspaceTab } from "./workspace";
import { loadMobileWorkspace, mobileRequest } from "./mobile";
import { clearDraft, draftStorage } from "./onboardingDraft";
import { AccountPanel } from "./AccountPanel";
import { DiscoveryPanel } from "./DiscoveryPanel";
import { discoveryContextKey } from "./discovery";
import type { DiscoverySnapshot } from "./discovery";
import { BillingPanel } from "./BillingPanel";
import { billingReturnLocation, billingRouteRequested } from "./billing";
import { consumeAuthRedirectError } from "./authRedirect";
import { isTrustPath, trustPageEnabled } from "./trustPage";
import { TrustPageUnpublished, TrustSafetyPage } from "./TrustSafetyPage";
import { WORKSPACE_TAB_LABELS } from "./workspace";
import { eligibilityLabel, eligibilityNextAction } from "./eligibilityDisplay";
import { assessmentDisplay } from "./assessmentDisplay";
import "./beta.css";
import "./pursuit-theme.css";

type View = WorkspaceTab | "profile";
type AuthState = "loading" | "signed_out" | "signed_in";

function humanizeEligibility(value: BetaJob["eligibility_status"]) {
  return eligibilityLabel(value);
}

export default function BetaApp() {
  if (typeof window !== "undefined" && isTrustPath(window.location.pathname)) {
    return trustPageEnabled ? <div className="pursuit-web"><TrustSafetyPage /></div> : <div className="pursuit-web"><TrustPageUnpublished /></div>;
  }
  return <div className="pursuit-web"><BetaSession /></div>;
}

function BetaSession() {
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [session, setSession] = useState<Session | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [privacyIntent, setPrivacyIntent] = useState(false);

  const priorUser = useRef<string | null>(null);
  const authRedirectError = useRef<string | null>(null);

  useEffect(() => {
    authRedirectError.current = consumeAuthRedirectError();
    if (authRedirectError.current) setError(authRedirectError.current);
  }, []);

  useEffect(() => {
    if (!betaEnabled) return;
    const client = requireSupabase();
    let active = true;
    let authEventReceived = false;
    const { data: subscription } = client.auth.onAuthStateChange((_event, nextSession) => {
      if (!active) return;
      if (priorUser.current && priorUser.current !== nextSession?.user.id) { clearDraft(draftStorage(), priorUser.current); setPrivacyIntent(false); }
      priorUser.current = nextSession?.user.id ?? null;
      authEventReceived = true;
      setSession(nextSession);
      setAuthState(nextSession ? "signed_in" : "signed_out");
      setNotice(null);
      if (nextSession) {
        authRedirectError.current = null;
        setError(null);
      } else {
        setError(authRedirectError.current);
      }
    });
    client.auth.getSession().then(({ data, error: sessionError }) => {
      if (!active || authEventReceived) return;
      if (sessionError) setError(authRedirectError.current || "We could not restore your session. Please sign in again.");
      else if (!data.session && authRedirectError.current) setError(authRedirectError.current);
      priorUser.current = data.session?.user.id ?? null;
      setSession(data.session);
      setAuthState(data.session ? "signed_in" : "signed_out");
    }).catch(() => {
      if (active && !authEventReceived) { setError(authRedirectError.current || "We could not check your session. Please try signing in again."); setAuthState("signed_out"); }
    });
    return () => { active = false; subscription.subscription.unsubscribe(); };
  }, []);

  if (!betaEnabled) return <BetaSetupNeeded />;
  if (authState === "loading") return <main className="beta-shell beta-center"><p>Checking your secure session…</p></main>;
  if (authState === "signed_out") return <BetaAuthLanding onNotice={setNotice} onError={setError} notice={notice} error={error} privacyIntent={privacyIntent} onPrivacyIntentChange={setPrivacyIntent} />;
  if (!session) return null;
  // Unmount all private state on account change; a stale request cannot reveal
  // the previous account's profile, jobs, documents or open sheets.
  return <SignedInWorkspace key={session.user.id} session={session} initialPrivacy={privacyIntent} />;
}

function SignedInWorkspace({ session, initialPrivacy = false }: { session: Session; initialPrivacy?: boolean }) {
  const [view, setView] = useState<View>("discover");
  const [privacyOpen, setPrivacyOpen] = useState(initialPrivacy);
  // A Checkout/portal return only reopens the billing view; the panel then asks
  // the server for status. The query is a hint, never payment proof. Keep this
  // initializer pure: StrictMode double-invokes it, and mutating history here
  // can drop the return flag before state is committed.
  const [billingOpen, setBillingOpen] = useState(() => (
    typeof window !== "undefined" && billingRouteRequested(window.location.search)
  ));
  useEffect(() => {
    if (typeof window === "undefined") return;
    const next = billingReturnLocation(window.location.search, window.location.pathname, window.location.hash);
    const current = window.location.pathname + window.location.search + window.location.hash;
    if (next.open && next.href !== current) {
      window.history.replaceState(window.history.state, "", next.href);
    }
  }, []);
  const [profile, setProfile] = useState<BetaProfile | null>(null);
  const [preferences, setPreferences] = useState<JobPreferences | null>(null);
  const [jobs, setJobs] = useState<BetaJob[]>([]);
  const [applications, setApplications] = useState<BetaApplication[]>([]);
  const [discoverySnapshot, setDiscoverySnapshot] = useState<DiscoverySnapshot | null>(null);
  const [studioJob, setStudioJob] = useState<BetaJob | null>(null);
  const [detailJob, setDetailJob] = useState<BetaJob | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const request = useRef<AbortController | null>(null);
  const loadWorkspace = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const timer = window.setTimeout(() => controller.abort(), 15000);
    setLoading(true); setError(null);
    try {
      const data = await loadMobileWorkspace(session.user.id, controller.signal);
      if (request.current !== controller) return;
      if (controller.signal.aborted) throw new Error("Your workspace could not be loaded. Check your connection and try again.");
      setProfile(data.profile);
      setPreferences(data.preferences);
      setJobs(data.jobs);
      setApplications(data.applications);
      setLoaded(true);
      return true;
    } catch (loadError) {
      if (request.current === controller) setError(loadError instanceof Error ? loadError.message : "Could not load your workspace.");
      return false;
    } finally {
      window.clearTimeout(timer);
      if (request.current === controller) setLoading(false);
    }
  }, [session.user.id]);
  useEffect(() => {
    if (!initialPrivacy) void loadWorkspace();
    return () => { request.current?.abort(); request.current = null; };
  }, [loadWorkspace, initialPrivacy]);
  const signOut = async () => {
    try {
      const { error: signOutError } = await requireSupabase().auth.signOut();
      if (signOutError) throw signOutError;
      clearDraft(draftStorage(), session.user.id);
    } catch { setError("Could not sign out. Check your connection and try again."); }
  };
  const groups = useMemo(() => groupRoles(jobs), [jobs]);
  const unavailableReadinessCount = applications.filter(row => row.readiness_unavailable === "packet_check_failed").length;
  const closeStudio = () => { setStudioJob(null); void loadWorkspace(); };
  // Privacy is auth-only and intentionally rendered before workspace/plan/onboarding gates.
  if (privacyOpen) return <main className="beta-shell"><AccountPanel session={session} onBack={() => { setPrivacyOpen(false); setLoaded(false); void loadWorkspace(); }} /></main>;
  if (billingOpen) return <main className="beta-shell"><BillingPanel userId={session.user.id} onBack={() => { setBillingOpen(false); setLoaded(false); void loadWorkspace(); }} onPrivacy={() => { setBillingOpen(false); setPrivacyOpen(true); }} /></main>;
  if (!loaded) return <main className="beta-shell beta-center" aria-busy={loading}><p className="beta-eyebrow">Your private workspace</p><h1>{loading ? "Picking up your story…" : "Let’s reconnect."}</h1><p role={error ? "alert" : "status"}>{error || "Loading your profile, saved roles and documents."}</p>{!loading && <button className="beta-primary" onClick={() => void loadWorkspace()}>Try again</button>}<button className="beta-secondary" onClick={() => setPrivacyOpen(true)}>Account privacy</button><p>Export and erasure requests do not require paid workspace access.</p><details className="pursuit-note"><summary>Operator tools (dormant)</summary><p>Invited accounts can check test billing without active workspace membership. Stripe stays off for design partners.</p><button className="beta-text-button" onClick={() => setBillingOpen(true)}>Open dormant billing panel</button></details><button className="beta-text-button" onClick={() => void signOut()}>Sign out</button></main>;
  if (editing) return <div className="pursuit-edit-profile"><button className="beta-text-button" onClick={() => setEditing(false)}>← Back to profile</button><button className="beta-text-button" onClick={() => setPrivacyOpen(true)}>Account privacy</button><OnboardingWizard key={session.user.id} session={session} profile={profile} preferences={preferences} onComplete={async () => { if (!await loadWorkspace()) throw new Error("Your changes were saved, but the workspace could not refresh. Please reconnect."); setEditing(false); setNotice("Your profile and preferences were saved."); }} /></div>;
  if (!profile?.onboarding_completed_at || !preferences) {
    return <div><button className="beta-text-button" onClick={() => void signOut()}>Sign out and clear this tab’s draft</button><button className="beta-text-button" onClick={() => setPrivacyOpen(true)}>Account privacy</button><OnboardingWizard session={session} profile={profile} preferences={preferences} onComplete={async () => {
      if (!await loadWorkspace()) throw new Error("Your changes were saved, but the workspace could not refresh. Please reconnect.");
      setView("discover");
      setNotice("Your workspace is ready. Finding roles using your saved preferences; no AI credits are used for this search.");
    }} /></div>;
  }

  return (
    <main className="beta-shell">
      <a className="pursuit-skip" href="#pursuit-workspace">Skip to workspace</a>
      <header className="beta-header">
        <a className="pursuit-wordmark" href="/beta" aria-label="The Job Pursuit home"><BrandIdentity subtitle="CAREER WORKSPACE" /></a>
        <div className="pursuit-header-actions"><button className="beta-text-button" onClick={() => void loadWorkspace()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button><button className="pursuit-profile-button" onClick={() => setView("profile")} aria-label="Your profile" aria-current={view === "profile" ? "page" : undefined}><WorkspaceIcon name="profile" /></button><button className="beta-text-button" onClick={() => void signOut()}>Sign out</button></div>
      </header>
      {notice && <p className="beta-notice" role="status">{notice}</p>}
      {error && <p className="beta-error" role="alert">{error}</p>}
      {unavailableReadinessCount > 0 && <div className="beta-notice" role="alert"><p>Readiness could not be verified for {unavailableReadinessCount} saved role{unavailableReadinessCount === 1 ? "" : "s"}. These roles are not marked Ready. Your profile, saved roles and documents remain available. Wait a moment, then retry; this check does not run AI or submit applications.</p><button className="beta-secondary" disabled={loading} onClick={() => void loadWorkspace()}>{loading ? "Checking readiness…" : "Retry readiness checks"}</button></div>}
      {(jobs.length === 200 || applications.length === 200) && <p className="beta-notice">Showing up to 200 recently updated records per list. Older records are not included in these counts.</p>}
      <div id="pursuit-workspace" tabIndex={-1}>
      {view === "discover" && <DiscoverView key={discoveryContextKey(session.user.id, profile, preferences)} profile={profile} snapshot={discoverySnapshot} onSnapshot={setDiscoverySnapshot} onReviewResume={() => setView("prepare")} allJobs={jobs} userId={session.user.id} preferences={preferences} onEditPreferences={() => setEditing(true)} onOpenStudio={setStudioJob} onJobsChanged={async () => { if (!await loadWorkspace()) throw new Error("Saved roles could not refresh. The completed save was not undone."); }} onOpenRank={() => setView("rank")} />}
      {view === "rank" && <RankView profile={profile} jobs={jobs} applications={applications} readyJobs={groups.ready} needsReview={groups.review} userId={session.user.id} onNavigate={setView} onOpenStudio={setStudioJob} onOpenDetail={setDetailJob} onJobsChanged={async () => { if (!await loadWorkspace()) throw new Error("Saved roles could not refresh. The completed save was not undone."); }} />}
      {view === "review" && <section className="beta-content"><ApplicationsWorkspace jobs={jobs} applications={applications} onOpenStudio={setStudioJob} onOpenDetail={setDetailJob} onFindRoles={() => setView("discover")} /></section>}
      {view === "prepare" && <section className="beta-content"><header className="pursuit-page-heading"><p className="beta-eyebrow">Prepare</p><h1>Grounded packets from your facts.</h1><p>Confirm source resumes, then open Application Studio for a saved role. Work rights and eligibility come before AI drafts. You apply on the employer site yourself.</p></header><DocumentsPanel session={session} onDiscover={() => setView("discover")} onChanged={async () => { await loadWorkspace(); }} /><div className="pursuit-note"><h2>Build on what’s true</h2><p>Confirm career facts, record work rights in Rank, then prepare drafts here. The product never submits to employers.</p><button className="beta-text-button" onClick={() => setView("profile")}>Review your profile →</button></div><JobSection title="Choose a role to prepare" jobs={activeRoles(jobs)} empty="Save a role in Discover, then open it here to prepare." onOpenStudio={setStudioJob} onOpenDetail={setDetailJob} /></section>}
      {view === "profile" && <><ProfileSummary profile={profile} preferences={preferences} onEdit={() => setEditing(true)} onDocuments={() => setView("prepare")} /><section className="beta-content"><div className="pursuit-note"><h2>Your account privacy</h2><p>Request a data export or account erasure and check its status. These controls are separate from any future paid access.</p><button className="beta-secondary" onClick={() => setPrivacyOpen(true)}>Account privacy</button></div><details className="pursuit-note"><summary>Operator tools (dormant)</summary><p>Stripe remains off for design partners. Test-mode billing status is available for invited operators only; it is not a live subscription offer.</p><button className="beta-text-button" onClick={() => setBillingOpen(true)}>Open dormant billing panel</button></details></section></>}
      </div>
      <nav className="beta-navigation" aria-label="Primary navigation">
        {WORKSPACE_TABS.map((item) => <button key={item} className={view === item ? "active" : ""} aria-current={view === item ? "page" : undefined} onClick={() => setView(item)}><WorkspaceIcon name={item} /><span>{WORKSPACE_TAB_LABELS[item]}</span></button>)}
      </nav>
      {studioJob && <WorkspaceDialog className="beta-studio-overlay" label={`Application studio for ${studioJob.title}`} onClose={closeStudio}><div className="beta-studio-modal"><button className="beta-text-button beta-studio-close" onClick={closeStudio}>Close</button><ApplicationStudio session={session} job={studioJob} onApplicationStatusChange={async () => {
        await loadWorkspace();
      }} /></div></WorkspaceDialog>}
      {detailJob && <JobDetailWorkspace job={detailJob} onClose={() => setDetailJob(null)} onOpenStudio={(job) => { setDetailJob(null); setStudioJob(job); }} />}
    </main>
  );
}

function BetaSetupNeeded() {
  return <main className="beta-shell beta-center"><p className="beta-eyebrow">THE JOB PURSUIT · BETA</p><h1>Private beta setup is in progress.</h1><p>This environment is intentionally separate from the founder’s personal dashboard. Configure the public Supabase URL and anonymous key to enable it.</p></main>;
}

function DiscoverView({ allJobs, userId, profile, snapshot, onSnapshot, onReviewResume, preferences, onEditPreferences, onOpenStudio, onJobsChanged, onOpenRank }: { allJobs: BetaJob[]; userId: string; profile: BetaProfile; snapshot: DiscoverySnapshot | null; onSnapshot: (snapshot: DiscoverySnapshot) => void; onReviewResume: () => void; preferences: JobPreferences; onEditPreferences: () => void; onOpenStudio: (job: BetaJob) => void; onJobsChanged: () => Promise<void>; onOpenRank: () => void }) {
  return <section className="beta-content"><header className="pursuit-page-heading"><p className="beta-eyebrow">Discover</p><h1>Roles for your next move.</h1><p>Search configured ATS boards against your preferences. Save roles to Rank for work-rights review, then Prepare grounded drafts. You apply externally yourself.</p></header><div className="discovery-view-toggle"><button className="beta-secondary" onClick={onOpenRank}>Open Rank ({activeRoles(allJobs).length} saved)</button></div><DiscoveryPanel userId={userId} profile={profile} snapshot={snapshot} onSnapshot={onSnapshot} onReviewResume={onReviewResume} preferences={preferences} savedJobs={allJobs} onSaved={onJobsChanged} onOpenStudio={onOpenStudio} onEditPreferences={onEditPreferences} /></section>;
}

function RankView({ profile, jobs, applications, readyJobs, needsReview, userId, onNavigate, onOpenStudio, onOpenDetail, onJobsChanged }: { profile: BetaProfile; jobs: BetaJob[]; applications: BetaApplication[]; readyJobs: BetaJob[]; needsReview: BetaJob[]; userId: string; onNavigate: (tab: WorkspaceTab) => void; onOpenStudio: (job: BetaJob) => void; onOpenDetail: (job: BetaJob) => void; onJobsChanged: () => Promise<void> }) {
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"all" | "ready" | "review">("all");
  if (activeRoles(jobs).length === 0 && !adding) {
    return <RankHome profile={profile} jobs={jobs} applications={applications} onNavigate={onNavigate} onOpenJob={onOpenDetail} />;
  }
  const matchesQuery = (job: BetaJob) => `${job.title} ${job.company_name} ${job.location_text ?? ""}`.toLowerCase().includes(query.trim().toLowerCase());
  const scopedJobs = activeRoles(jobs).filter((job) => matchesQuery(job) && (scope === "all" || (scope === "ready" ? job.status === "ready" : job.eligibility_status === "needs_review")));
  const { ready: scopedReady, review: scopedReview, saved: scopedSaved } = groupRoles(scopedJobs);
  const nextJob = needsReview[0] ?? readyJobs[0];
  const focusLabel = needsReview.length > 0
    ? `${needsReview.length} role${needsReview.length === 1 ? "" : "s"} need work-rights review`
    : readyJobs.length > 0
      ? `${readyJobs.length} role${readyJobs.length === 1 ? "" : "s"} ready to prepare`
      : "Your shortlist is clear";

  return (
    <section className="beta-content">
      <div className="beta-today-heading">
        <div>
          <p className="beta-eyebrow">Rank</p>
          <h1>Your shortlist.</h1>
          <p className="beta-subtitle">Work rights are your self-report and come before AI fit. Prepare only after you record eligibility for the posting. You apply on the employer site yourself.</p>
        </div>
        <button className="beta-secondary" onClick={() => setAdding((value) => !value)}>{adding ? "Close" : "Add a job link"}</button>
      </div>

      <section className="beta-focus-card" aria-label="Rank focus">
        <div>
          <p className="beta-focus-card__eyebrow">Focus</p>
          <h3>{focusLabel}</h3>
          <p>{nextJob ? `${eligibilityLabel(nextJob.eligibility_status)}. ${eligibilityNextAction(nextJob.eligibility_status)}` : "Save an employer posting in Discover or add a link below."}</p>
        </div>
        {nextJob ? <button className="beta-primary" onClick={() => needsReview.length > 0 ? onOpenStudio(nextJob) : onOpenDetail(nextJob)}>{needsReview.length > 0 ? "Record work rights" : "Review role"}</button> : <button className="beta-primary" onClick={() => onNavigate("discover")}>Open Discover</button>}
      </section>

      {adding && <AddJobForm userId={userId} onSaved={async (job) => { setAdding(false); await onJobsChanged(); onOpenStudio(job); }} />}

      <div className="beta-stats" aria-label="Shortlist summary">
        <article><strong>{needsReview.length}</strong><span>Need work-rights review</span></article>
        <article><strong>{readyJobs.length}</strong><span>Ready to prepare</span></article>
        <article><strong>{activeRoles(jobs).length}</strong><span>Saved opportunities</span></article>
      </div>

      <div className="beta-role-search">
        <label><span className="beta-visually-hidden">Search saved roles</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search role, company, or location" /></label>
        <div className="beta-role-search__filters" aria-label="Role list filter">
          <button aria-pressed={scope === "all"} className={scope === "all" ? "active" : ""} onClick={() => setScope("all")}>All</button>
          <button aria-pressed={scope === "review"} className={scope === "review" ? "active" : ""} onClick={() => setScope("review")}>Needs work rights</button>
          <button aria-pressed={scope === "ready"} className={scope === "ready" ? "active" : ""} onClick={() => setScope("ready")}>Ready</button>
        </div>
      </div>

      {scopedReview.length > 0 && <JobSection title="Action required — work rights" jobs={scopedReview} empty="No eligibility questions are blocking you." onOpenStudio={onOpenStudio} onOpenDetail={onOpenDetail} />}
      {scopedReady.length > 0 && <JobSection title="Ready to prepare" jobs={scopedReady} empty="" onOpenStudio={onOpenStudio} onOpenDetail={onOpenDetail} />}
      {scopedJobs.length === 0 && <div className="beta-empty"><h2>{query || scope !== "all" ? "No roles match this view" : "Your next chapter starts here"}</h2><p>{query || scope !== "all" ? "Try a different search or return to all roles." : "Save an employer posting in Discover to begin ranking."}</p><button className="beta-secondary" onClick={() => { if (query || scope !== "all") { setQuery(""); setScope("all"); } else { onNavigate("discover"); } }}>{query || scope !== "all" ? "Clear filters" : "Open Discover"}</button></div>}
      {scopedSaved.length > 0 && <JobSection title="Other saved roles" jobs={scopedSaved} empty="" onOpenStudio={onOpenStudio} onOpenDetail={onOpenDetail} />}
    </section>
  );
}

function AddJobForm({ userId, onSaved }: { userId: string; onSaved: (job: BetaJob) => Promise<void> }) {
  const [company, setCompany] = useState("");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [location, setLocation] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setSaving(true); setError(null);
    try {
      const postingUrl = safePostingUrl(url);
      if (!postingUrl || !company.trim() || !title.trim() || !description.trim()) throw new Error("Add a company, role title, full description and valid HTTP or HTTPS posting link.");
      const job = await mobileRequest<BetaJob>(userId, "/jobs", { method: "POST", body: { source_url: postingUrl, company_name: company.trim(), title: title.trim(), location_text: location.trim() || null, description: description.trim() } });
      await onSaved(job);
    } catch (saveError) { setError(saveError instanceof Error ? saveError.message : "Could not save this role."); }
    finally { setSaving(false); }
  };
  return <form className="beta-add-job" onSubmit={save}><h2>Add a role to your workspace</h2><p>Paste the actual posting text. We do not search the market or fetch the URL. An existing posting opens its saved workspace; review its description before preparing.</p><fieldset disabled={saving} className="workflow-fieldset"><div className="beta-form-grid"><label>Company<input required maxLength={160} value={company} onChange={(event) => setCompany(event.target.value)} /></label><label>Role title<input required maxLength={160} value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="wide">Original posting URL<input required type="url" maxLength={2048} value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://careers.company.com/..." /></label><label>Location <span>Optional</span><input maxLength={300} value={location} onChange={(event) => setLocation(event.target.value)} /></label><label className="wide">Full job description<textarea required maxLength={80000} rows={8} value={description} onChange={event => setDescription(event.target.value)} /></label></div><button className="beta-primary">{saving ? "Saving…" : "Save role and open Prepare"}</button></fieldset>{error && <p className="beta-error" role="alert">{error}</p>}</form>;
}

function ProfileSummary({ profile, preferences, onEdit, onDocuments }: { profile: BetaProfile; preferences: JobPreferences; onEdit: () => void; onDocuments: () => void }) { return <section className="beta-content"><header className="pursuit-page-heading"><p className="beta-eyebrow">Your profile</p><h1>Your story, on your terms.</h1><p>Keep your details and preferences up to date.</p></header><button className="beta-secondary" onClick={onEdit}>Edit profile and preferences</button><dl className="beta-profile"><div><dt>Name</dt><dd>{profile.display_name || "Not provided"}</dd></div><div><dt>Base location</dt><dd>{profile.base_location || "Not provided"}</dd></div><div><dt>Target roles</dt><dd>{preferences.target_titles.join(", ") || "Not provided"}</dd></div><div><dt>Regions</dt><dd>{preferences.preferred_regions.join(", ") || "Open"}</dd></div><div><dt>Sponsorship</dt><dd>{preferences.sponsorship_required ? "Required for relocation" : "Not currently required"}</dd></div></dl><div className="pursuit-note"><h2>Your career foundation</h2><p>Career text is explicitly reviewed and self-reported. Structured qualifications saved on mobile are preserved; this web editor does not change those fields.</p><p className="beta-profile-career">{profile.career_text || "No confirmed career facts yet. Edit your profile or review extracted resume facts in Prepare."}</p><button className="beta-text-button" onClick={onDocuments}>Manage your source resumes →</button></div></section>; }

function JobSection({ title, jobs, empty, onOpenStudio, onOpenDetail }: { title: string; jobs: BetaJob[]; empty: string; onOpenStudio: (job: BetaJob) => void; onOpenDetail: (job: BetaJob) => void }) {
  return <section className="beta-section"><h2>{title}</h2>{jobs.length === 0 ? <p className="beta-empty-inline">{empty}</p> : <div className="beta-job-list">{jobs.map((job) => <article className="beta-job" key={job.id}><div><span className={`beta-badge ${job.eligibility_status}`}>{humanizeEligibility(job.eligibility_status)}</span><h3>{job.title}</h3><p>{job.company_name} · {job.location_text || job.workplace_type || "Location not listed"}</p><p className="beta-job-eligibility-hint">{eligibilityNextAction(job.eligibility_status)}</p>{job.score != null && <small>{assessmentDisplay(job)}</small>}{job.last_validated_at && <small>Validated {new Date(job.last_validated_at).toLocaleDateString()}</small>}</div><div className="beta-job-actions"><button className="beta-secondary" onClick={() => onOpenDetail(job)}>View role</button><button className="beta-secondary" onClick={() => onOpenStudio(job)}>Prepare</button>{safePostingUrl(job.source_url) && <a href={safePostingUrl(job.source_url)!} target="_blank" rel="noreferrer">Original ↗</a>}</div></article>)}</div>}</section>;
}
