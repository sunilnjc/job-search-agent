import { BrandIdentity } from "./BrandIdentity";

export { isTrustPath, trustPageEnabled } from "./trustPage";

export function TrustSafetyPage() {
  return (
    <main className="beta-shell" id="trust-safety">
      <header className="beta-header">
        <a className="pursuit-wordmark" href="/beta" aria-label="The Job Pursuit home"><BrandIdentity subtitle="TRUST AND SAFETY" /></a>
      </header>
      <article className="beta-content workflow-stack">
        <header className="pursuit-page-heading">
          <p className="beta-eyebrow">Beta · draft</p>
          <h1>How The Job Pursuit treats your data and AI help.</h1>
          <p>This page describes the current beta workspace. It is not a legal contract, a compliance certification, or a claim that every hosted control has been independently audited.</p>
        </header>

        <section className="workflow-card">
          <h2>Data isolation</h2>
          <p>Each signed-in account has a private workspace. Profile, resumes, saved roles, generated drafts, and application notes are stored under that account. Another customer cannot read your rows through the product API; access is checked against the authenticated user and database row-level policies.</p>
          <p>The public beta is separate from the founder’s personal dashboard. Operator tools that aggregate counts use a service role and are not available in the browser. We do not sell your career documents.</p>
        </section>

        <section className="workflow-card">
          <h2>No automatic employer submit</h2>
          <p>The beta prepares drafts. It does not submit applications to employers for you. Automatic submission is off. You review documents, open the employer’s own site, and send the application yourself. Recording a status of submitted in the tracker is your note, not proof that an employer received anything.</p>
        </section>

        <section className="workflow-card">
          <h2>Export and delete</h2>
          <p>Signed-in accounts can request a data export and can request account erasure from Account privacy. Export and erasure do not require a paid plan. Erasure is queued work, not an instant “everything is gone” claim in the browser. Download an export before you request deletion. Details and limits are listed in Account privacy after you sign in.</p>
        </section>

        <section className="workflow-card">
          <h2>Honest limits of the AI</h2>
          <p>Fit scores are overlap estimates against the facts you confirmed, not ATS scores, hiring probabilities, or legal advice. Draft resumes and cover letters restate confirmed source facts; they can still miss nuance, omit important experience, or be a poor match for a specific employer form.</p>
          <p>Work eligibility is your self-report unless you have independent advice. The assistant will not invent credentials, change eligibility, or promise an interview. You remain responsible for what you send to an employer.</p>
        </section>

        <p><a className="beta-text-button" href="/beta">Back to beta sign-in</a></p>
      </article>
    </main>
  );
}

export function TrustPageUnpublished() {
  return (
    <main className="beta-shell beta-center">
      <p className="beta-eyebrow">THE JOB PURSUIT · BETA</p>
      <h1>This trust page is not published in this environment.</h1>
      <p>The draft exists in the product, but the operator has not enabled it. <a href="/beta">Return to beta</a>.</p>
    </main>
  );
}
