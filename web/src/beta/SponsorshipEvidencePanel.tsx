import { sponsorshipExcerpts } from "./sponsorshipEvidence";
import { safePostingUrl } from "./workspace";

export function SponsorshipEvidence({ description, sourceUrl, truncated = false }: {
  description: string; sourceUrl: string | null; truncated?: boolean;
}) {
  const excerpts = sponsorshipExcerpts(description);
  const url = safePostingUrl(sourceUrl ?? "");
  return <section className="workflow-card" aria-label="Sponsorship evidence">
    <h4>{excerpts.length ? "Sponsorship / work-rights wording to review" : "Sponsorship support not established"}</h4>
    <p>Job-specific employer support and your personal work rights are separate. We do not verify H-1B eligibility or guarantee sponsorship or interviews.</p>
    {excerpts.length ? <details><summary>Read posting excerpts ({excerpts.length})</summary>
      <p>Quotes from the supplied posting, not verified employer commitments. Review context, conditions and any conflicting wording.</p>
      {excerpts.map((excerpt, i) => <blockquote key={i}>{excerpt}</blockquote>)}
    </details> : <p>No explicit sponsorship keywords were found in the supplied text. That does not mean the employer refuses sponsorship.</p>}
    {truncated && <p className="beta-notice">Posting text is incomplete. Missing wording cannot establish an employer policy.</p>}
    <p>Employer sponsorship history has not been checked. Past sponsorship would not confirm support for this vacancy.</p>
    <p>Ask the recruiter whether this specific role supports your requested employer change. Get qualified immigration advice for your individual situation.</p>
    {url && <a href={url} target="_blank" rel="noreferrer">Check original posting ↗</a>}
  </section>;
}
