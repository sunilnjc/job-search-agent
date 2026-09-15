import { useEffect, useState } from "react";
import { mobileRequest } from "./mobile";
import { MobileApiError } from "./mobileTransport";

type Source = { id: string; kind: "candidate" | "job"; text: string };
type Review = {
  version: "document-review-v1"; mode: "selected_verbatim_facts"; notice: string;
  snapshot: { generation_id: string; captured_at: string; fingerprint: string; sources: Source[] };
  resume_sections: Record<string, string[]>; cover_letter_source_ids: string[];
  omitted_source_ids: string[]; omitted_with_literal_job_overlap: string[]; requirements_needing_review: string[]; unattributed_source_ids: string[];
};
type SavedReview = { model_run_id: string; created_at: string; review: Review };
type Props = { userId: string; jobId: string; refreshKey?: string | number };
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const SOURCE_ID = /^(?:career_text\.\d+|answers\.\d+|career_background\.qualifications\.\d+|profile\.(?:headline|summary|skills|experience|employment|education|certifications|projects|achievements|languages)(?:\.(?:\d+|[a-z_]{1,40}))*|job\.(?:title|company_name|location_text|workplace_type|employment_type|requirements\.\d+))$/;
const HEADINGS = new Set(["Professional Summary", "Work Experience", "Education", "Skills", "Projects", "Qualifications", "Languages", "Volunteer Experience", "Selected Career Contributions"]);
const ERROR = "Saved document evidence could not be verified. Refresh the review or inspect the downloaded draft and original source before sharing.";
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
function string(value: unknown, maximum: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) throw new Error(ERROR);
  return value;
}
function ids(value: unknown, maximum = 400): string[] {
  if (!Array.isArray(value) || value.length > maximum) throw new Error(ERROR);
  const result = value.map(ref => string(ref, 180));
  if (new Set(result).size !== result.length || result.some(ref => !SOURCE_ID.test(ref))) throw new Error(ERROR);
  return result;
}
function date(value: unknown): string {
  const result = string(value, 64);
  if (!Number.isFinite(Date.parse(result))) throw new Error(ERROR);
  return result;
}
function unavailableCount(value: unknown): number {
  if (!record(value) || typeof value.unavailable_count !== "number"
    || !Number.isInteger(value.unavailable_count) || value.unavailable_count < 0 || value.unavailable_count > 20) throw new Error(ERROR);
  return value.unavailable_count;
}

/** Fail closed on unresolved IDs; never fetch today's profile to interpret them. */
export function checkedDocumentReviews(value: unknown): SavedReview[] {
  if (!record(value) || !Array.isArray(value.reviews) || value.reviews.length > 20) throw new Error(ERROR);
  if (value.reviews.length + unavailableCount(value) > 20) throw new Error(ERROR);
  const seen = new Set<string>();
  return value.reviews.map(raw => {
    if (!record(raw) || !record(raw.review)) throw new Error(ERROR);
    const modelRunId = string(raw.model_run_id, 36);
    if (!UUID.test(modelRunId) || seen.has(modelRunId)) throw new Error(ERROR);
    seen.add(modelRunId);
    const r = raw.review;
    if (new TextEncoder().encode(JSON.stringify(r)).byteLength > 256000 || r.version !== "document-review-v1"
      || r.mode !== "selected_verbatim_facts" || !record(r.snapshot) || !record(r.resume_sections)) throw new Error(ERROR);
    const s = r.snapshot;
    const generationId = string(s.generation_id, 36);
    const fingerprint = string(s.fingerprint, 64);
    if (!UUID.test(generationId) || !/^[a-f0-9]{64}$/.test(fingerprint)
      || !Array.isArray(s.sources) || !s.sources.length || s.sources.length > 400) throw new Error(ERROR);
    const sources = new Map<string, Source>();
    s.sources.forEach(rawSource => {
      if (!record(rawSource)) throw new Error(ERROR);
      const id = ids([rawSource.id], 1)[0];
      const kind: Source["kind"] = id.startsWith("job.") ? "job" : "candidate";
      if (sources.has(id) || rawSource.kind !== kind) throw new Error(ERROR);
      sources.set(id, { id, kind, text: string(rawSource.text, 3000) });
    });
    const sections: Record<string, string[]> = {};
    const resumeIds: string[] = [];
    const entries = Object.entries(r.resume_sections);
    if (!entries.length || entries.length > HEADINGS.size) throw new Error(ERROR);
    for (const [heading, rawIds] of entries) {
      if (!HEADINGS.has(heading)) throw new Error(ERROR);
      const section = ids(rawIds, 28);
      if (!section.length) throw new Error(ERROR);
      sections[heading] = section;
      resumeIds.push(...section);
    }
    if (resumeIds.length > 28 || new Set(resumeIds).size !== resumeIds.length) throw new Error(ERROR);
    const letter = ids(r.cover_letter_source_ids, 4);
    const omitted = ids(r.omitted_source_ids);
    const overlaps = ids(r.omitted_with_literal_job_overlap);
    const requirements = ids(r.requirements_needing_review, 100);
    const unattributed = ids(r.unattributed_source_ids, 32);
    const selected = new Set([...resumeIds, ...letter]);
    const candidateIds = new Set([...sources.values()].filter(source => source.kind === "candidate").map(source => source.id));
    if (!letter.length || [...selected, ...omitted].some(ref => !candidateIds.has(ref))
      || omitted.some(ref => selected.has(ref)) || selected.size + omitted.length !== candidateIds.size
      || overlaps.some(ref => !omitted.includes(ref))
      || unattributed.some(ref => !selected.has(ref))
      || requirements.some(ref => !ref.startsWith("job.requirements.") || sources.get(ref)?.kind !== "job")) throw new Error(ERROR);
    return {
      model_run_id: modelRunId, created_at: date(raw.created_at),
      review: { version: "document-review-v1", mode: "selected_verbatim_facts", notice: string(r.notice, 600),
        snapshot: { generation_id: generationId, captured_at: date(s.captured_at), fingerprint, sources: [...sources.values()] },
        resume_sections: sections, cover_letter_source_ids: letter, omitted_source_ids: omitted,
        omitted_with_literal_job_overlap: overlaps, requirements_needing_review: requirements, unattributed_source_ids: unattributed },
    };
  });
}

function sourceLabel(ref: string): string {
  if (ref.startsWith("career_text.")) return "Confirmed career notes";
  if (ref.startsWith("career_background.qualifications.")) return "Self-reported qualification, not independently verified";
  if (ref.startsWith("answers.")) return "Confirmed answer and its question";
  if (ref.startsWith("job.requirements.")) return "Job posting excerpt";
  if (ref.startsWith("job.")) return "Saved job information";
  return "Confirmed profile detail";
}

/** Scope key destroys old-owner/old-job state immediately, even during fetches. */
export function DocumentReviewPanel(props: Props) {
  return <DocumentReviewSession key={`${props.userId}:${props.jobId}`} {...props} />;
}
function DocumentReviewSession({ userId, jobId, refreshKey = 0 }: Props) {
  const [reviews, setReviews] = useState<SavedReview[]>([]);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(0);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    if (!UUID.test(jobId)) { setError("Choose a saved job before reviewing its documents."); setLoading(false); return; }
    void mobileRequest<unknown>(userId, `/jobs/${jobId}/document-reviews`, { signal: controller.signal })
      .then(value => {
        if (controller.signal.aborted) return;
        const next = checkedDocumentReviews(value);
        setReviews(next);
        setUnavailable(unavailableCount(value));
        setSelected(previous => next.some(row => row.model_run_id === previous) ? previous : next[0]?.model_run_id ?? "");
      })
      .catch(cause => {
        if (controller.signal.aborted) return;
        setReviews([]); setSelected(""); setUnavailable(0);
        setError(cause instanceof MobileApiError && cause.status === 404
          ? "This service version does not provide saved document reviews yet. Your downloaded drafts may still exist; review their original sources before sharing."
          : cause instanceof Error ? cause.message : ERROR);
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [userId, jobId, refreshKey, reload]);
  const chosen = reviews.find(row => row.model_run_id === selected);
  return <section className="workflow-card workflow" aria-label="Document evidence review" aria-busy={loading}>
    <h3>What went into this draft?</h3>
    <p>See the exact facts selected for each document and the facts left out. This is an extractive draft—not a rewritten or independently verified resume.</p>
    {loading && <p role="status">Loading private generation-time evidence…</p>}
    {error && <p className="beta-error" role="alert">{error}</p>}
    <button type="button" disabled={loading} onClick={() => setReload(value => value + 1)}>Refresh document reviews</button>
    {!loading && !error && !reviews.length && unavailable === 0 && <p>No source review was saved for this job yet. Older drafts may predate this feature. Their source IDs cannot safely be reconstructed from your current profile.</p>}
    {!loading && !error && unavailable > 0 && <p className="beta-notice" role="status">Evidence is unavailable or inconsistent for {unavailable} of the recent saved generations. This is not an all-clear: inspect their downloaded drafts and original sources before sharing.</p>}
    {!loading && reviews.length > 0 && <label>Saved generation
      <select value={selected} onChange={event => setSelected(event.target.value)}>
        {reviews.map((row, index) => <option key={row.model_run_id} value={row.model_run_id}>
          {index === 0 ? "Most recent · " : ""}{new Date(row.review.snapshot.captured_at).toLocaleString()} · {row.model_run_id.slice(0, 8)}
        </option>)}
      </select>
    </label>}
    {!loading && chosen && <ReviewSnapshot review={chosen.review} />}
  </section>;
}

function ReviewSnapshot({ review }: { review: Review }) {
  const sources = new Map(review.snapshot.sources.map(source => [source.id, source]));
  const overlaps = new Set(review.omitted_with_literal_job_overlap);
  const sourceList = (refs: string[], omitted = false) => <ul>{refs.map(ref => {
    const source = sources.get(ref)!; // validated against this exact saved snapshot above
    return <li key={ref} style={{ marginBlock: 12, overflowWrap: "anywhere" }}>
      <p className="workflow-prose">{source.text}</p>
      <small>{sourceLabel(ref)}{omitted && overlaps.has(ref) ? " · Some words also occur in the job; this is not proof of fit." : ""}{review.unattributed_source_ids.includes(ref) ? " · Employer attribution unconfirmed." : ""}</small>
    </li>;
  })}</ul>;
  const title = sources.get("job.title")?.text;
  const company = sources.get("job.company_name")?.text;
  return <div className="workflow-stack" style={{ marginTop: 20 }}>
    <div>
      <p className="beta-notice">Generation-time snapshot, saved {new Date(review.snapshot.captured_at).toLocaleString()}. Later profile or job edits do not change these facts. This review does not mark an application ready or submit anything.</p>
      {(title || company) && <p><strong>Target at generation:</strong> {[title, company].filter(Boolean).join(" — ")}</p>}
      <p>{review.notice}</p>
      {review.unattributed_source_ids.length > 0 && <p className="beta-notice">Some selected contributions were not explicitly linked to an employer in your source notes. They are shown separately so they are not attributed to the last role. Confirm the employer context before sharing.</p>}
    </div>
    <div><h4>Resume selections</h4><p>Shown by exported section. Contact-header details are not included in this body-fact review.</p>
      {Object.entries(review.resume_sections).map(([heading, refs]) => <details key={heading} open>
        <summary>{heading} · {refs.length} fact{refs.length === 1 ? "" : "s"}</summary>{sourceList(refs)}
      </details>)}
    </div>
    <details open><summary>Cover-letter selections · {review.cover_letter_source_ids.length} facts</summary>
      <p>The remaining salutation, opening and closing are fixed rhetorical wording—not additional candidate or company evidence.</p>
      {sourceList(review.cover_letter_source_ids)}
    </details>
    <details><summary>Not selected for either document · {review.omitted_source_ids.length} facts</summary>
      <p>Omission is not a judgment that a fact is false or unimportant. Check for important missing evidence. A credential not held or a limitation must never be turned into a positive claim.</p>
      {review.omitted_source_ids.length ? sourceList(review.omitted_source_ids, true) : <p>All eligible candidate facts in this snapshot were selected in at least one document.</p>}
    </details>
    <details><summary>Flagged requirement excerpts · {review.requirements_needing_review.length}</summary>
      <p>These statements were flagged for review or recalled directly from the posting when a comparison was missing or rejected. They are not eligibility clearance. Also check the Studio’s pending questions and readiness state.</p>
      {review.requirements_needing_review.length ? sourceList(review.requirements_needing_review) : <p>No requirement excerpts are listed here. This does not establish that every requirement is met.</p>}
    </details>
    <details><summary>Snapshot provenance</summary>
      <p>This fingerprint detects inconsistent stored evidence; it is not verification of the facts by an employer or regulator.</p>
      <p style={{ overflowWrap: "anywhere" }}>Generation: <code>{review.snapshot.generation_id}</code><br />Fingerprint: <code>{review.snapshot.fingerprint}</code></p>
    </details>
  </div>;
}
