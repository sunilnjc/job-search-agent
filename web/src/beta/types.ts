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
  duplicate?: boolean;
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
};

export type BetaResume = {
  id: string; label: string; original_filename: string; byte_size: number;
  is_default: boolean; created_at?: string;
};
export type BetaArtifact = {
  id: string; job_id: string; resume_id: string | null; kind: string;
  filename: string; mime_type: string; byte_size: number; created_at: string;
};
export type BetaQuestion = {
  id: string; job_id: string | null; prompt: string; answer: string | null;
  status: string; remember: boolean;
};
export type BetaWorkspace = {
  profile: BetaProfile | null; preferences: JobPreferences | null;
  jobs: BetaJob[]; applications: BetaApplication[]; resumes: BetaResume[];
  artifacts: BetaArtifact[]; questions: BetaQuestion[];
  capabilities: { job_detail_fetch?: boolean; bootstrap_list_limit?: number };
};
