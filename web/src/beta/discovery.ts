import { safePostingUrl } from "./workspace.ts";
import type { BetaProfile, JobPreferences } from "./types.ts";
export type DiscoveryFilters = { titles: string[]; locations: string[]; workplace_type: "any" | "remote" | "hybrid" | "onsite" };
export type DiscoveryRequest = { query: string; filters: DiscoveryFilters; limit: 20 };
export type DiscoveryJob = {
  source_id: string; source: string; provider: string; board: string; external_id: string;
  title: string; company_name: string; company_name_is_board_identifier?: boolean;
  source_url: string; description: string; location_text: string | null; country?: string | null;
  department?: string | null; employment_type?: string | null; workplace_type: "remote" | "hybrid" | "onsite" | "unknown";
  content_truncated: boolean; fetched_at: string; source_published_at?: string | null; source_created_at?: string | null; source_updated_at?: string | null;
  match_reasons: string[]; eligibility_status: "unknown";
  eligibility: { status: "unknown"; provisional: true; independently_verified: false; reasons: string[] };
  persisted: false;
  relevance?: { method: "profile_rules_v1"; score: number; reasons: string[]; gaps: string[]; review_required: boolean };
};
export type DiscoverySource = {
  source: string; status: "ok" | "partial" | "error"; cached: boolean;
  fetched_at: string | null; checked_at: string; truncated: boolean;
  received_count: number; returned_count: number; dropped_count: number; unlisted_count: number; duplicate_count: number;
  error_code?: string | null; retry_after?: number;
};
export type DiscoveryResponse = {
  status: "ok" | "partial" | "unavailable"; results: DiscoveryJob[]; sources: DiscoverySource[];
  partial: boolean; truncated: boolean; matched_count: number; returned_count: number; searched_at: string;
  persisted: false; eligibility_verified: false;
  warnings?: string[];
  ranking?: string;
};
/** Private, in-memory only. Invalidate on owner, career facts or preference changes. */
export type DiscoverySnapshot = { key: string; result: DiscoveryResponse; receivedAt: number };
export function discoveryContextKey(userId: string, profile: BetaProfile, preferences: JobPreferences) {
  return JSON.stringify([userId, profile.career_text ?? "", profile.career_background ?? null,
    preferences.target_titles, preferences.preferred_locations, preferences.preferred_regions,
    preferences.remote_preference, preferences.sponsorship_required, preferences.work_authorization_notes,
    preferences.discovery_rules ?? null]);
}
export function reusableDiscovery(snapshot: DiscoverySnapshot | null, key: string, now = Date.now()): DiscoveryResponse | null {
  return snapshot?.key === key && now >= snapshot.receivedAt && now - snapshot.receivedAt < 300000 ? snapshot.result : null;
}
const text = (value: unknown, max: number) => typeof value === "string" && value.length <= max;
const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const textList = (value: unknown) => Array.isArray(value) && value.length <= 50 && value.every(item => text(item, 2000));
export function discoveryRequest(query: string, titles: string, locations: string, workplace: string): DiscoveryRequest {
  const list = (value: string) => value.split(",").map(item => item.trim()).filter(Boolean);
  const body = { query: query.trim(), filters: { titles: list(titles), locations: list(locations), workplace_type: workplace }, limit: 20 };
  if (body.query.length > 160 || [body.filters.titles, body.filters.locations].some(items => items.length > 10 || items.some(item => item.length > 160)) || !["any", "remote", "hybrid", "onsite"].includes(workplace)) throw new Error("Use a query up to 160 characters and up to 10 terms per filter, each up to 160 characters.");
  if (new TextEncoder().encode(JSON.stringify(body)).byteLength > 8192) throw new Error("Search filters exceed the 8 KiB request limit. Shorten the query or filter lists.");
  return body as DiscoveryRequest;
}
export function checkedDiscoveryResponse(value: unknown): DiscoveryResponse {
  const data = value as DiscoveryResponse | null;
  const invalid = () => new Error("Discovery returned an unexpected result. No role was saved. Search again or contact the service operator.");
  if (!data || !["ok", "partial", "unavailable"].includes(data.status) || data.persisted !== false || data.eligibility_verified !== false || !Array.isArray(data.results) || data.results.length > 50 || !Array.isArray(data.sources) || data.sources.length > 8
    || !count(data.matched_count) || !count(data.returned_count) || data.returned_count !== data.results.length || typeof data.partial !== "boolean" || typeof data.truncated !== "boolean" || !text(data.searched_at, 100)
    || (data.warnings !== undefined && !textList(data.warnings))) throw invalid();
  for (const job of data.results) {
    if (!job || !text(job.source_id, 100) || !/^ats_[a-f0-9]{64}$/.test(job.source_id) || ![job.title, job.company_name].every(item => text(item, 160) && item.trim()) || !text(job.description, 16000) || !job.description.trim()
      || !text(job.source_url, 2048) || !safePostingUrl(job.source_url) || !text(job.source, 200) || !text(job.provider, 100) || !text(job.board, 100)
      || !(job.location_text === null || text(job.location_text, 300)) || !["remote", "hybrid", "onsite", "unknown"].includes(job.workplace_type)
      || typeof job.content_truncated !== "boolean" || !text(job.fetched_at, 100) || !textList(job.match_reasons) || job.persisted !== false || job.eligibility_status !== "unknown"
      || !job.eligibility || job.eligibility.status !== "unknown" || job.eligibility.independently_verified !== false || job.eligibility.provisional !== true || !textList(job.eligibility.reasons)
      || [job.source_published_at, job.source_created_at, job.source_updated_at].some(value => value != null && !text(value, 100))) throw invalid();
    if (job.relevance !== undefined && (!job.relevance || job.relevance.method !== "profile_rules_v1" || !Number.isFinite(job.relevance.score)
      || job.relevance.score < 0 || job.relevance.score > 100 || !textList(job.relevance.reasons) || !textList(job.relevance.gaps)
      || typeof job.relevance.review_required !== "boolean")) throw invalid();
  }
  for (const source of data.sources) {
    if (!source || !text(source.source, 200) || !["ok", "partial", "error"].includes(source.status) || typeof source.cached !== "boolean" || typeof source.truncated !== "boolean"
      || ![source.received_count, source.returned_count, source.dropped_count, source.unlisted_count, source.duplicate_count].every(count)
      || !(source.fetched_at === null || text(source.fetched_at, 100)) || !text(source.checked_at, 100) || (source.error_code != null && !text(source.error_code, 200)) || (source.retry_after !== undefined && !count(source.retry_after))) throw invalid();
  }
  if (new Set(data.results.map(job => job.source_id)).size !== data.results.length || (data.status === "unavailable" && data.results.length)) throw invalid();
  return data;
}
/** Public source_id is never a saved-job UUID, owner ID, or an eligibility claim. */
export function discoverySavePayload(job: DiscoveryJob) {
  const sourceUrl = safePostingUrl(job.source_url);
  if (!sourceUrl || !job.description.trim() || !job.title.trim() || !job.company_name.trim()) throw new Error("This posting is missing a valid source link or required role text. Add it manually after reviewing the employer posting.");
  return { source_url: sourceUrl, title: job.title, company_name: job.company_name, description: job.description, location_text: job.location_text || null };
}
