import { useCallback, useEffect, useMemo, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { requireSupabase } from "./supabase";
import type { BetaJob } from "./types";

type ApplicationStatus = "draft" | "ready" | "submitted" | "interviewing" | "rejected" | "withdrawn" | "closed";
type StudioStep = "overview" | "documents" | "questions" | "review";

type ApplicationRecord = {
  id: string;
  user_id: string;
  job_id: string;
  status: ApplicationStatus;
  applied_at: string | null;
  submission_url: string | null;
  notes: string | null;
  updated_at: string;
};

type Artifact = {
  id: string;
  application_id: string | null;
  kind: "tailored_resume" | "cover_letter" | "answer_packet" | "other";
  filename: string;
  mime_type: string | null;
  byte_size: number | null;
  model_provider: string | null;
  model_name: string | null;
  created_at: string;
};

export type ApplicationStudioProps = {
  session: Session;
  job: BetaJob;
  onApplicationStatusChange?: (application: ApplicationRecord) => void;
};

const STEPS: Array<{ id: StudioStep; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "documents", label: "Documents" },
  { id: "questions", label: "Questions" },
  { id: "review", label: "Final review" },
];

function messageFor(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function formatBytes(value: number | null) {
  if (value == null) return "Size unavailable";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * A tenant-scoped application workspace. This component only records the
 * user's local application state; it never opens, fills, or submits an
 * employer form. Supabase RLS is the ownership boundary for every query.
 */
export function ApplicationStudio({ session, job, onApplicationStatusChange }: ApplicationStudioProps) {
  const [activeStep, setActiveStep] = useState<StudioStep>("overview");
  const [application, setApplication] = useState<ApplicationRecord | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadStudio = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const client = requireSupabase();
      const { data: applicationData, error: applicationError } = await client
        .from("applications")
        .upsert(
          {
            user_id: session.user.id,
            job_id: job.id,
            submission_url: job.source_url,
          },
          { onConflict: "user_id,job_id" },
        )
        .select("id, user_id, job_id, status, applied_at, submission_url, notes, updated_at")
        .single();
      if (applicationError) throw applicationError;

      const nextApplication = applicationData as ApplicationRecord;
      setApplication(nextApplication);

      const { data: artifactData, error: artifactError } = await client
        .from("artifacts")
        .select("id, application_id, kind, filename, mime_type, byte_size, model_provider, model_name, created_at")
        .eq("user_id", session.user.id)
        .eq("job_id", job.id)
        .order("created_at", { ascending: false });
      if (artifactError) throw artifactError;
      setArtifacts((artifactData ?? []) as Artifact[]);
    } catch (loadError) {
      setError(messageFor(loadError, "Could not load this private application workspace."));
    } finally {
      setIsLoading(false);
    }
  }, [job.id, job.source_url, session.user.id]);

  useEffect(() => {
    void loadStudio();
  }, [loadStudio]);

  const readiness = useMemo(() => {
    const tailoredResume = artifacts.some((artifact) => artifact.kind === "tailored_resume");
    const coverLetter = artifacts.some((artifact) => artifact.kind === "cover_letter");
    const answerPacket = artifacts.some((artifact) => artifact.kind === "answer_packet");
    const hasEligiblePosting = job.eligibility_status === "eligible";

    return {
      tailoredResume,
      coverLetter,
      answerPacket,
      hasEligiblePosting,
      canMarkReady: tailoredResume && coverLetter && hasEligiblePosting,
    };
  }, [artifacts, job.eligibility_status]);

  const updateStatus = async (status: ApplicationStatus, successMessage: string) => {
    if (!application) return;
    setIsUpdating(true);
    setError(null);
    setNotice(null);
    try {
      const changes: Partial<ApplicationRecord> = { status };
      if (status === "submitted") changes.applied_at = new Date().toISOString();
      const { data, error: updateError } = await requireSupabase()
        .from("applications")
        .update(changes)
        .eq("id", application.id)
        .eq("user_id", session.user.id)
        .select("id, user_id, job_id, status, applied_at, submission_url, notes, updated_at")
        .single();
      if (updateError) throw updateError;
      const updated = data as ApplicationRecord;
      setApplication(updated);
      onApplicationStatusChange?.(updated);
      setNotice(successMessage);
    } catch (updateError) {
      setError(messageFor(updateError, "Could not update the application status."));
    } finally {
      setIsUpdating(false);
    }
  };

  const markReady = async () => {
    if (!readiness.canMarkReady) return;
    await updateStatus("ready", "Package marked ready. Review it before completing the employer application.");
  };

  const confirmExternalSubmission = async () => {
    if (!application || application.status !== "ready") return;
    const confirmed = window.confirm(
      `Confirm that you have personally submitted your application to ${job.company_name}.\n\nThis only updates your private tracker. It does not send anything to the employer.`,
    );
    if (!confirmed) return;
    await updateStatus("submitted", "Marked submitted in your private tracker. No employer form was submitted by this app.");
  };

  const actionRequired = [
    !readiness.hasEligiblePosting && "Eligibility has not been confirmed for this role. Review work authorization, location, and sponsorship requirements before proceeding.",
    !readiness.tailoredResume && "A tailored resume has not been prepared for this application.",
    !readiness.coverLetter && "A cover letter has not been prepared for this application.",
    !readiness.answerPacket && "No approved answer packet is available. Employer questions, especially subjective or unknown ones, require your review and input.",
  ].filter(Boolean) as string[];

  return (
    <section className="beta-application-studio" aria-labelledby="application-studio-title" aria-busy={isLoading}>
      <header className="beta-application-studio__header">
        <p className="beta-eyebrow">Private application studio</p>
        <h2 id="application-studio-title">{job.title}</h2>
        <p>{job.company_name}{job.location_text ? ` · ${job.location_text}` : ""}</p>
        <p className="beta-application-studio__scope">This workspace is private to your account. It never submits an employer application.</p>
      </header>

      <div className="beta-application-studio__feedback" aria-live="polite" aria-atomic="true">
        {error && <p className="beta-message beta-message--error" role="alert">{error}</p>}
        {notice && <p className="beta-message beta-message--success">{notice}</p>}
      </div>

      {isLoading ? (
        <p className="beta-application-studio__status">Preparing your private application workspace…</p>
      ) : !application ? (
        <div className="beta-application-studio__empty" role="status">
          <p>This application workspace could not be created.</p>
          <button type="button" className="beta-button" onClick={() => void loadStudio()}>Try again</button>
        </div>
      ) : (
        <>
          <nav className="beta-application-studio__steps" aria-label="Application studio sections">
            {STEPS.map((step) => (
              <button
                key={step.id}
                type="button"
                role="tab"
                aria-selected={activeStep === step.id}
                aria-controls={`application-studio-${step.id}`}
                id={`application-studio-tab-${step.id}`}
                className={activeStep === step.id ? "is-active" : undefined}
                onClick={() => setActiveStep(step.id)}
              >
                {step.label}
              </button>
            ))}
          </nav>

          {activeStep === "overview" && (
            <div id="application-studio-overview" role="tabpanel" aria-labelledby="application-studio-tab-overview">
              <h3>Readiness</h3>
              <ul className="beta-application-studio__checks">
                <li>{readiness.hasEligiblePosting ? "Ready: eligibility is confirmed." : "Action required: eligibility is not confirmed."}</li>
                <li>{readiness.tailoredResume ? "Ready: tailored resume available." : "Action required: tailored resume missing."}</li>
                <li>{readiness.coverLetter ? "Ready: cover letter available." : "Action required: cover letter missing."}</li>
                <li>{readiness.answerPacket ? "Ready: answer packet available." : "Action required: answer packet missing."}</li>
              </ul>
              <p>Current package status: <strong>{application.status}</strong>.</p>
            </div>
          )}

          {activeStep === "documents" && (
            <div id="application-studio-documents" role="tabpanel" aria-labelledby="application-studio-tab-documents">
              <h3>Application artifacts</h3>
              {artifacts.length === 0 ? (
                <p>No document metadata is available yet. This beta does not generate documents in this screen.</p>
              ) : (
                <ul className="beta-application-studio__artifacts" aria-label="Application document metadata">
                  {artifacts.map((artifact) => (
                    <li key={artifact.id}>
                      <strong>{artifact.filename}</strong>
                      <span>{artifact.kind.replace(/_/g, " ")} · {formatBytes(artifact.byte_size)} · {formatDate(artifact.created_at)}</span>
                      {(artifact.model_provider || artifact.model_name) && <span>Prepared with {artifact.model_provider ?? "an approved provider"}{artifact.model_name ? ` / ${artifact.model_name}` : ""}.</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {activeStep === "questions" && (
            <div id="application-studio-questions" role="tabpanel" aria-labelledby="application-studio-tab-questions">
              <h3>Application questions</h3>
              <p>Only approved, evidence-grounded answers may be used. This screen does not infer or invent facts.</p>
              {readiness.answerPacket ? <p>An approved answer packet is listed in Documents. Review each answer against the employer’s live form.</p> : <p className="beta-application-studio__action-required">Action required: the beta does not yet collect, answer, or submit employer questions from this screen.</p>}
            </div>
          )}

          {activeStep === "review" && (
            <div id="application-studio-review" role="tabpanel" aria-labelledby="application-studio-tab-review">
              <h3>Final review</h3>
              <p>Confirm the role is still live, the documents are correct, and every employer response is truthful before you use the external application site.</p>
              {actionRequired.length > 0 && (
                <div className="beta-application-studio__action-required" role="alert">
                  <h4>Action required</h4>
                  <ul>{actionRequired.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
              )}
              <div className="beta-application-studio__actions">
                <button type="button" className="beta-button" onClick={() => void markReady()} disabled={!readiness.canMarkReady || isUpdating || application.status === "submitted"}>
                  {isUpdating ? "Saving…" : "Mark package ready"}
                </button>
                <button type="button" className="beta-button" onClick={() => void confirmExternalSubmission()} disabled={application.status !== "ready" || isUpdating}>
                  Confirm external submission
                </button>
              </div>
              <p className="beta-application-studio__limitation">Confirm external submission only records your confirmation after a browser dialog. It never sends documents, clicks an ATS form, or submits to an employer.</p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
