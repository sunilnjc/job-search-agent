export type FitExplanation = {
  why: string[];
  evidence: string[];
  uncertainty: string[];
};

export type BetaProfile = {
  user_id: string;
  display_name: string | null;
  phone: string | null;
  base_location: string | null;
  onboarding_completed_at: string | null;
  career_text?: string | null;
  career_background?: Record<string, unknown> | null;
};

export type JobPreferences = {
  user_id: string;
  target_titles: string[];
  preferred_locations: string[];
  preferred_regions: string[];
  remote_preference: "remote_only" | "hybrid" | "onsite" | "open";
  sponsorship_required: boolean;
  work_authorization_notes: string | null;
  minimum_match_score?: number;
  discovery_rules?: {
    remote_country_policy: "review" | "require_explicit";
    remote_country_codes: string[];
    sponsorship_policy: "review" | "require_explicit";
  };
};

export type BetaJob = {
  id: string;
  source: string;
  source_url: string;
  company_name: string;
  title: string;
  location_text: string | null;
  workplace_type: "remote" | "hybrid" | "onsite" | "unknown" | null;
  eligibility_status: "unknown" | "eligible" | "ineligible" | "needs_review";
  status: "new" | "matched" | "ready" | "applied" | "excluded" | "archived";
  last_validated_at: string | null;
  description?: string | null;
  score?: number | null;
  rationale?: string | null;
  fit_explanation?: FitExplanation | null;
  duplicate?: boolean;
  application_status?: BetaApplication["status"];
  readiness_unavailable?: "capped_workspace" | "packet_check_failed";
  eligibility_review?: {
    status: "eligible" | "ineligible" | "unknown";
    reason: string;
    confirmed: boolean;
    confirmed_at: string;
    provenance: "user_self_report";
    independently_verified: false;
  } | null;
};

export type BetaApplication = {
  id: string;
  user_id: string;
  job_id: string;
  status: "draft" | "ready" | "submitted" | "interviewing" | "rejected" | "withdrawn" | "closed";
  applied_at: string | null;
  updated_at: string;
  notes?: string | null;
  recorded_status?: BetaApplication["status"];
  readiness?: BetaReadiness;
  readiness_unavailable?: "capped_workspace" | "packet_check_failed";
};

export type BetaResume = {
  id: string; label: string; original_filename: string; byte_size: number;
  is_default: boolean; created_at?: string;
};
export type BetaArtifact = {
  id: string; job_id: string; resume_id: string | null; kind: string;
  filename: string; mime_type: string; byte_size: number; created_at: string;
};
export type BetaPacket = {
  key: string; run_id: string; resume_id: string; variant: string; format: "pdf" | "docx";
  generated_at: string; current: boolean; issue: string | null;
  context_fingerprint: string; packet_fingerprint: string; source_sha256: string;
  artifacts: [BetaArtifact & { sha256: string }, BetaArtifact & { sha256: string }];
};
export type BetaReadiness = {
  user_id: string; job_id: string; version: "packet-v1"; packets: BetaPacket[];
  review: { id: string; run_id: string; resume_artifact_id: string; letter_artifact_id: string;
    packet_fingerprint: string; reviewed_at: string; current: boolean } | null;
  ready: boolean; reason: string | null; pending_questions: number;
  application_status: BetaApplication["status"]; recorded_status: BetaApplication["status"] | null;
  application_id: string | null;
};
export type BetaQuestion = {
  id: string; job_id: string | null; prompt: string; answer: string | null;
  status: string; remember: boolean;
};
export type BetaWorkspace = {
  profile: BetaProfile | null; preferences: JobPreferences | null;
  jobs: BetaJob[]; applications: BetaApplication[]; resumes: BetaResume[];
  artifacts: BetaArtifact[]; questions: BetaQuestion[];
  capabilities: { job_detail_fetch?: boolean; bootstrap_list_limit?: number; packet_readiness?: string };
};
