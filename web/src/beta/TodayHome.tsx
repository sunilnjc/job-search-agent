import type { BetaApplication, BetaJob, BetaProfile } from "./types";
import { activeRoles, applicationCounts, groupRoles } from "./workspace";
import type { WorkspaceTab } from "./workspace";
import { WorkspaceIcon } from "./WorkspaceIcon";

type Props = {
  profile: BetaProfile;
  jobs: BetaJob[];
  applications: BetaApplication[];
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenJob: (job: BetaJob) => void;
};

export function TodayHome({ profile, jobs, applications, onNavigate, onOpenJob }: Props) {
  const roles = activeRoles(jobs);
  const { review } = groupRoles(jobs);
  const counts = applicationCounts(applications);
  const name = profile.display_name?.trim().split(/\s+/)[0] || "there";
  return <section className="beta-content pursuit-home">
    <header className="pursuit-page-heading">
      <p className="beta-eyebrow">{new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(new Date())}</p>
      <h1>Your next move, {name}.</h1>
      <p>Find relevant roles, prepare a stronger application, and keep your next steps together.</p>
    </header>
    <div className="pursuit-home-grid">
      <section className="pursuit-focus" aria-labelledby="pursuit-focus-title">
        <p className="beta-eyebrow">Your focus</p>
        <h2 id="pursuit-focus-title">Your next role<br />starts here.</h2>
        <p>Discover checks selected employer boards against your saved preferences and reviewed career facts. No AI credits are used for discovery.</p>
        <button className="pursuit-focus-action" onClick={() => onNavigate("discover")}>Find roles for me <WorkspaceIcon name="arrow" /></button>
      </section>
      <div className="pursuit-home-side">
        <div className="pursuit-metrics">
          <button onClick={() => onNavigate("discover")}><WorkspaceIcon name="discover" /><strong>{roles.length}</strong><span>Saved opportunities</span></button>
          <button onClick={() => onNavigate("tracker")}><WorkspaceIcon name="tracker" /><strong>{counts.active}</strong><span>Applications in progress</span></button>
        </div>
        <section className="pursuit-note">
          <p className="beta-eyebrow">One step at a time</p>
          <h2>{review.length ? "A little clarity goes a long way." : "Keep your search intentional."}</h2>
          <p>{review.length ? `${review.length} saved role${review.length === 1 ? " needs" : "s need"} an eligibility review. Check the posting before you apply.` : "Save a role that interests you, check the requirements, and keep your documents close."}</p>
          <button className="beta-text-button" onClick={() => onNavigate("discover")}>{review.length ? "Review saved roles" : "Explore your workspace"} <span aria-hidden="true">→</span></button>
        </section>
      </div>
    </div>
    <section className="pursuit-recent" aria-labelledby="pursuit-recent-title">
      <div className="pursuit-section-heading"><div><h2 id="pursuit-recent-title">Pick up where you left off</h2><p>Your explicitly saved roles. Discover can search configured public boards; Studio helps you assess and prepare after saving.</p></div><button className="beta-text-button" onClick={() => onNavigate("discover")}>See all</button></div>
      {roles.length ? <div className="pursuit-opportunities">{roles.slice(0, 2).map(job => <button className="pursuit-opportunity" key={job.id} onClick={() => onOpenJob(job)}>
        <div className="pursuit-company"><span className="pursuit-company-mark" aria-hidden="true">{job.company_name.slice(0, 2).toUpperCase()}</span><span><strong>{job.company_name}</strong><small>{job.location_text || "Location not listed"}</small></span><WorkspaceIcon name="arrow" /></div>
        <h3>{job.title}</h3><span className="pursuit-chip">{job.eligibility_status === "eligible" ? "Eligibility recorded" : "Eligibility to verify"}</span>
      </button>)}</div> : <div className="beta-empty"><h3>Find your first promising role</h3><p>Open Discover to find recommendations. Save a role to review its fit and prepare your application.</p><button className="beta-secondary" onClick={() => onNavigate("discover")}>Explore recommendations</button></div>}
    </section>
  </section>;
}
