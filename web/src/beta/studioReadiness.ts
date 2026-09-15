import type { BetaApplication, BetaArtifact, BetaJob, BetaPacket, BetaQuestion, BetaReadiness, BetaWorkspace } from "./types.ts";

// Legacy grouping is retained for historical display/tests only. Current Studio
// readiness uses serverStudioPackets; filenames NEVER certify current context.
const GENERATED = /^(tailored_resume|cover_letter)-(role_aligned|career_change|sse|fde)-(\d{8}T\d{12}Z)-([a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12})\.(pdf|docx)$/;
const MIME = { pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" };
export type StudioPacket = {
  key: string; generatedAt: string; variant: string; artifacts: BetaArtifact[];
  pair: [BetaArtifact, BetaArtifact] | null; issue: string | null;
  server?: BetaPacket;
};
export type PacketReview = { packetKey: string; filesKey: string; contextKey: string; packetFingerprint?: string };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
const APP_STATUSES = ["draft", "ready", "submitted", "interviewing", "rejected", "withdrawn", "closed"];
export function checkedReadiness(value: unknown, userId: string, jobId: string): BetaReadiness {
  const bad = () => { throw new Error("The server returned invalid packet readiness. Refresh before reviewing; no Ready record was saved."); };
  if (!value || typeof value !== "object") return bad();
  const s = value as BetaReadiness;
  if (s.user_id !== userId || s.job_id !== jobId || s.version !== "packet-v1" || typeof s.ready !== "boolean"
    || !APP_STATUSES.includes(s.application_status) || !(s.recorded_status === null || APP_STATUSES.includes(s.recorded_status))
    || !(s.application_id === null || UUID.test(s.application_id))
    || !Number.isSafeInteger(s.pending_questions) || s.pending_questions < 0 || s.pending_questions > 201
    || ![null, "packet_review_required", "review_stale", "pending_questions", "eligibility_required", "too_many_records"].includes(s.reason)
    || !Array.isArray(s.packets) || s.packets.length > 400) return bad();
  const keys = new Set<string>();
  for (const p of s.packets) {
    if (!p || !UUID.test(p.run_id) || !UUID.test(p.resume_id) || !["pdf", "docx"].includes(p.format)
      || p.key !== `${p.run_id}:${p.format}` || keys.has(p.key) || typeof p.current !== "boolean"
      || !["role_aligned", "career_change", "sse", "fde"].includes(p.variant)
      || ![null, "source_missing", "facts_missing", "too_many_records", "generation_context_changed"].includes(p.issue)
      || p.current !== (p.issue === null) || !Number.isFinite(Date.parse(p.generated_at))
      || ![p.packet_fingerprint, p.context_fingerprint, p.source_sha256].every(h => typeof h === "string" && HASH.test(h))
      || !Array.isArray(p.artifacts) || p.artifacts.length !== 2) return bad();
    keys.add(p.key);
    for (const [i, a] of p.artifacts.entries()) {
      if (!a || !UUID.test(a.id) || a.job_id !== jobId || a.resume_id !== p.resume_id || !HASH.test(a.sha256)
        || a.kind !== ["tailored_resume", "cover_letter"][i] || a.mime_type !== MIME[p.format]
        || !Number.isSafeInteger(a.byte_size) || a.byte_size <= 0 || a.byte_size > 12 * 1024 * 1024
        || typeof a.filename !== "string" || !a.filename || a.filename.length > 255) return bad();
    }
    if (p.artifacts[0].id === p.artifacts[1].id) return bad();
  }
  if (s.review !== null) {
    const r = s.review;
    if (!r || ![r.id, r.run_id, r.resume_artifact_id, r.letter_artifact_id].every(id => typeof id === "string" && UUID.test(id))
      || typeof r.current !== "boolean" || !HASH.test(r.packet_fingerprint) || !Number.isFinite(Date.parse(r.reviewed_at))) return bad();
  }
  if (s.ready && (s.application_status !== "ready" || s.recorded_status !== "ready" || !s.application_id
    || s.pending_questions !== 0 || s.reason !== null || !s.review?.current
    || !s.packets.some(p => durablePacketReviewed(s, p)))) return bad();
  if (!s.ready && s.application_status === "ready") return bad();
  return s;
}

export function durablePacketReviewed(state: BetaReadiness | null, packet: BetaPacket): boolean {
  const r = state?.review;
  return Boolean(state?.ready && r?.current && packet.current && r.run_id === packet.run_id
    && r.packet_fingerprint === packet.packet_fingerprint && r.resume_artifact_id === packet.artifacts[0].id
    && r.letter_artifact_id === packet.artifacts[1].id);
}

export function serverStudioPackets(state: BetaReadiness | null, resumeId: string, variant: string): StudioPacket[] {
  return (state?.packets ?? []).filter(p => p.resume_id === resumeId && p.variant === variant).map(p => ({
    key: p.key, generatedAt: p.generated_at, variant: p.variant, artifacts: p.artifacts, pair: p.artifacts,
    issue: p.current ? null : "This generation no longer matches the saved career facts, preferences, source or job. Prepare a current packet before marking ready.", server: p,
  }));
}

function generatedFile(artifact: BetaArtifact) {
  const match = typeof artifact.filename === "string" && GENERATED.exec(artifact.filename);
  if (!match || artifact.kind !== match[1] || artifact.mime_type !== MIME[match[5] as keyof typeof MIME]
    || !artifact.id || !Number.isSafeInteger(artifact.byte_size) || artifact.byte_size <= 0) return null;
  const [, , variant, stamp, generation, format] = match;
  const iso = `${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6, 8)}T${stamp.slice(9, 11)}:${stamp.slice(11, 13)}:${stamp.slice(13, 15)}.${stamp.slice(15, 18)}Z`;
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime()) || date.toISOString() !== iso) return null;
  return { variant, stamp, generation, format, generatedAt: iso };
}

export function studioPackets(artifacts: BetaArtifact[], jobId: string, resumeId: string, variant: string): StudioPacket[] {
  const groups = new Map<string, StudioPacket>();
  const seenIds = new Set<string>();
  const duplicateIds = new Set<string>();
  for (const artifact of artifacts) {
    if (seenIds.has(artifact.id)) duplicateIds.add(artifact.id);
    seenIds.add(artifact.id);
    const file = generatedFile(artifact);
    if (!file || artifact.job_id !== jobId || !resumeId || artifact.resume_id !== resumeId || file.variant !== variant) continue;
    const key = JSON.stringify([jobId, resumeId, file.variant, file.stamp, file.generation]);
    const group = groups.get(key) ?? { key, generatedAt: file.generatedAt, variant, artifacts: [], pair: null, issue: null };
    group.artifacts.push(artifact); groups.set(key, group);
  }
  for (const group of groups.values()) {
    const slots = new Map<string, BetaArtifact>();
    for (const artifact of group.artifacts) {
      const slot = `${artifact.kind}:${generatedFile(artifact)!.format}`;
      if (slots.has(slot) || duplicateIds.has(artifact.id)) group.issue = "Ambiguous duplicate files in this generation. This packet cannot be marked ready.";
      slots.set(slot, artifact);
    }
    if (!group.issue) {
      for (const format of ["pdf", "docx"]) {
        const resume = slots.get(`tailored_resume:${format}`), letter = slots.get(`cover_letter:${format}`);
        if (resume && letter) { group.pair = [resume, letter]; break; }
      }
      if (!group.pair) group.issue = "Incomplete generation: no resume and cover letter in the same format. Historical files are not combined.";
    }
    group.artifacts.sort((a, b) => a.id.localeCompare(b.id));
  }
  return [...groups.values()].sort((a, b) => b.generatedAt.localeCompare(a.generatedAt) || a.key.localeCompare(b.key));
}

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value !== null && typeof value === "object") return `{${Object.entries(value).filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`).join(",")}}`;
  return JSON.stringify(value ?? null);
}
function fields(value: unknown, names: string[]) {
  const object = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return Object.fromEntries(names.map(name => [name, object[name] ?? null]));
}

export function studioQuestions(workspace: BetaWorkspace, jobId: string, extra: BetaQuestion[] = []) {
  return [...new Map([...workspace.questions, ...extra].filter(q => q.job_id === jobId || q.job_id === null).map(q => [q.id, q])).values()];
}

// In-memory equality token only. Never persist/display/log the candidate data,
// or pretend this browser snapshot is the server's generation context hash.
export function studioContextKey(userId: string, workspace: BetaWorkspace, job: BetaJob, resumeId: string, variant: string) {
  return stable({ userId, variant,
    profile: fields(workspace.profile, ["user_id", "display_name", "phone", "base_location", "timezone", "career_text", "career_background"]),
    preferences: fields(workspace.preferences, ["user_id", "target_titles", "preferred_locations", "preferred_regions", "remote_preference", "sponsorship_required", "work_authorization_notes", "minimum_match_score"]),
    job: fields(job, ["id", "source_url", "title", "company_name", "description", "location_text", "workplace_type", "employment_type", "eligibility_review"]),
    resume: fields(workspace.resumes.find(r => r.id === resumeId), ["id", "original_filename", "byte_size", "created_at", "updated_at"]),
    questions: studioQuestions(workspace, job.id).sort((a, b) => a.id.localeCompare(b.id)).map(q => fields(q, ["id", "prompt", "answer", "status", "remember"])),
  });
}

export function readinessReadIssue(workspace: BetaWorkspace): string | null {
  const limit = workspace.capabilities?.bootstrap_list_limit ?? 200;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 200) return "The workspace list limit could not be verified. Refresh before reviewing a packet.";
  if (!workspace.profile || !workspace.preferences) return "Profile or preferences are missing. Refresh and complete them before reviewing a packet.";
  for (const name of ["artifacts", "questions", "resumes", "applications"] as const) {
    const rows = workspace[name];
    if (!Array.isArray(rows) || rows.length >= limit) return `The ${name} list may be incomplete. Current readiness cannot be established from a capped or missing list.`;
    if (rows.some(row => !row || typeof row.id !== "string" || !row.id) || new Set(rows.map(row => row.id)).size !== rows.length) return `The ${name} list contains ambiguous records. Refresh before reviewing a packet.`;
  }
  return null;
}

export function packetReview(packet: StudioPacket, contextKey: string): PacketReview | null {
  if (!packet.pair || packet.issue) return null;
  return { packetKey: packet.key, contextKey, packetFingerprint: packet.server?.packet_fingerprint,
    filesKey: stable(packet.pair.map(a => fields(a, ["id", "job_id", "resume_id", "filename", "kind", "mime_type", "byte_size", "created_at"]))) };
}
export function reviewMatches(review: PacketReview | null, packet: StudioPacket | undefined, contextKey: string) {
  const expected = packet && packetReview(packet, contextKey);
  return Boolean(review && expected && review.packetKey === expected.packetKey && review.contextKey === expected.contextKey
    && review.filesKey === expected.filesKey && review.packetFingerprint === expected.packetFingerprint);
}

export function readinessIssue(workspace: BetaWorkspace, job: BetaJob, resumeId: string, packet: StudioPacket | undefined): string | null {
  const readIssue = readinessReadIssue(workspace);
  if (readIssue) return readIssue;
  if (!workspace.resumes.some(r => r.id === resumeId)) return "The selected source resume is no longer available.";
  if (!workspace.profile?.career_text?.trim() || !job.description?.trim()) return "Confirmed career facts and a saved job description are required.";
  if (!packet) return "Select one identified generation to review. Legacy files without generation metadata remain downloadable but cannot be paired safely.";
  if (!packet.pair || packet.issue) return packet.issue ?? "A complete same-generation packet is required.";
  if (job.eligibility_review?.status !== "eligible" || job.eligibility_review.confirmed !== true) return "A current eligible self-report is required.";
  if (studioQuestions(workspace, job.id).some(q => q.status !== "answered")) return "Resolve the outstanding questions before marking ready.";
  return null;
}

// A confirmed write must notify the parent even when one refresh fails. The API
// derives jobs/applications consistently; never add a second jobs.status write.
export async function refreshAfterApplicationSave(refreshStudio: () => Promise<unknown>, refreshParent: () => Promise<unknown>) {
  return Promise.allSettled([Promise.resolve().then(refreshStudio), Promise.resolve().then(refreshParent)]);
}
export function latestApplication(rows: BetaApplication[], jobId: string, acknowledged: BetaApplication | null) {
  const row = rows.find(a => a.job_id === jobId);
  // A read-derived stale receipt can downgrade Ready without updating the manual
  // record's timestamp. Never resurrect the cached Ready on timestamp equality.
  if (row?.readiness?.version === "packet-v1" || row?.readiness_unavailable) return row;
  if (!acknowledged || acknowledged.job_id !== jobId) return row;
  if (row && Number.isFinite(Date.parse(row.updated_at)) && Date.parse(row.updated_at) > Date.parse(acknowledged.updated_at)) return row;
  return acknowledged;
}
