export const STATUSES = [
  "new",
  "matched",
  "drafted",
  "applied",
  "interviewing",
  "rejected",
  "offer",
] as const;

export type Status = (typeof STATUSES)[number];

export type Eligibility =
  | "worldwide"
  | "sponsors"
  | "restricted"
  | "no-sponsorship"
  | "unknown"
  | "title-filtered";

export interface Job {
  id: number;
  title: string;
  company: string;
  location: string;
  remote: boolean;
  country: string | null;
  url: string;
  description: string;
  salary: string | null;
  status: Status;
  llm_score: number | null;
  llm_reasoning: string | null;
  embedding_similarity: number | null;
  eligibility: Eligibility;
  excluded_reason: string | null;
}

export interface JobDetail extends Job {
  cover_letter: string | null;
  resume_tailoring: string | null;
  gap_analysis: string | null;
  has_resume_docx: boolean;
  has_resume_pdf: boolean;
  has_cover_letter_pdf: boolean;
}

export interface RunStatus {
  run_id: string;
  status: "running" | "done" | "error";
  log: string[];
}

export type StatusSummary = Record<Status, number>;

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}
