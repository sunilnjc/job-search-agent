export type BetaProfile = {
  user_id: string;
  display_name: string | null;
  phone: string | null;
  base_location: string | null;
  onboarding_completed_at: string | null;
};

export type JobPreferences = {
  user_id: string;
  target_titles: string[];
  preferred_locations: string[];
  preferred_regions: string[];
  remote_preference: "remote_only" | "hybrid" | "onsite" | "open";
  sponsorship_required: boolean;
  work_authorization_notes: string | null;
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
};
