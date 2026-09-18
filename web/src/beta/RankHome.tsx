import type { BetaApplication, BetaJob, BetaProfile } from "./types";
import { activeRoles, applicationCounts, groupRoles } from "./workspace";
import type { WorkspaceTab } from "./workspace";
import { WorkspaceIcon } from "./WorkspaceIcon";
import { eligibilityLabel, eligibilityNextAction } from "./eligibilityDisplay";

type Props = {
  profile: BetaProfile;
  jobs: BetaJob[];
  applications: BetaApplication[];
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenJob: (job: BetaJob) => void;
};

/** Rank tab coaching: eligibility-first next actions for the saved shortlist. */
export function RankHome({ profile, jobs, applications, onNavigate, onOpenJob }: Props) {
  const roles = activeRoles(jobs);
  const { review } = groupRoles(jobs);
  const counts = applicationCounts(applications);
  const name = profile.display_name?.trim().split(/\s+/)[0] || "there";
  const nextReview = review[0] ?? roles.find(job => job.eligibility_status === "unknown") ?? roles[0];
  return <section className="beta-content pursuit-home">
    <header className="pursuit-page-heading">
      <p className="beta-eyebrow">Rank</p>
      <h1>Your shortlist, {name}.</h1>
      <p>Work rights come first. AI fit is secondary and never confirms eligibility.</p>
    </header>
    <div className="pursuit-home-grid">
      <section className="pursuit-focus" aria-labelledby="pursuit-focus-title">
        <p className="beta-eyebrow">Next in the loop</p>
        <h2 id="pursuit-focus-title">{review.length ? "Resolve work rights." : roles.length ? "Review before you prepare." : "Find roles to rank."}</h2>
        <p>{review.length
          ? `${review.length} saved role${review.length === 1 ? "" : "s"} still need a work-rights answer before Prepare.`
          : roles.length
            ? "Open a saved role to confirm eligibility, then prepare a grounded packet."
            : "Discover searches configured employer boards against your preferences. No AI credits are used for discovery."}</p>
        <button className="pursuit-focus-action" onClick={() => nextReview ? onOpenJob(nextReview) : onNavigate("discover")}>
          {nextReview ? "Open next role" : "Find roles"} <WorkspaceIcon name="arrow" />
        </button>
      </section>
      <div className="pursuit-home-side">
        <div className="pursuit-metrics">
          <button onClick={() => onNavigate("discover")}><WorkspaceIcon name="discover" /><strong>{roles.length}</strong><span>Saved opportunities</span></button>
          <button onClick={() => onNavigate("review")}><WorkspaceIcon name="review" /><strong>{counts.active}</strong><span>Packets in review</span></button>
        </div>
        <section className="pursuit-note">
          <p className="beta-eyebrow">Eligibility first</p>
          <h2>{review.length ? "A little clarity goes a long way." : "Keep the loop intentional."}</h2>
          <p>{review.length
            ? eligibilityNextAction("needs_review")
            : "Save a role in Discover, record work rights in Rank, prepare drafts, then apply on the employer site yourself."}</p>
          <button className="beta-text-button" onClick={() => onNavigate(review.length ? "rank" : "discover")}>
            {review.length ? "See roles needing review" : "Go to Discover"} <span aria-hidden="true">→</span>
          </button>
        </section>
      </div>
    </div>
    <section className="pursuit-recent" aria-labelledby="pursuit-recent-title">
      <div className="pursuit-section-heading">
        <div>
          <h2 id="pursuit-recent-title">Saved roles</h2>
          <p>Eligibility status is your self-report for each posting. It is not verified by AI.</p>
        </div>
        <button className="beta-text-button" onClick={() => onNavigate("discover")}>Discover more</button>
      </div>
      {roles.length ? <div className="pursuit-opportunities">{roles.slice(0, 4).map(job => <button className="pursuit-opportunity" key={job.id} onClick={() => onOpenJob(job)}>
        <div className="pursuit-company"><span className="pursuit-company-mark" aria-hidden="true">{job.company_name.slice(0, 2).toUpperCase()}</span><span><strong>{job.company_name}</strong><small>{job.location_text || "Location not listed"}</small></span><WorkspaceIcon name="arrow" /></div>
        <h3>{job.title}</h3>
        <span className={`pursuit-chip eligibility-${job.eligibility_status}`}>{eligibilityLabel(job.eligibility_status)}</span>
      </button>)}</div> : <div className="beta-empty"><h3>Nothing to rank yet</h3><p>Open Discover to find ATS board roles that match your preferences, then save the ones worth ranking.</p><button className="beta-secondary" onClick={() => onNavigate("discover")}>Open Discover</button></div>}
    </section>
  </section>;
}
