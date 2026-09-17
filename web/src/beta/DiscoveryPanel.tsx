import { useEffect, useRef, useState } from "react";
import type { BetaJob, BetaProfile, JobPreferences } from "./types";
import { mobileRequest } from "./mobile";
import { MobileApiError } from "./mobileTransport";
import { checkedDiscoveryResponse, discoveryContextKey, discoveryRequest, discoverySavePayload, reusableDiscovery } from "./discovery";
import type { DiscoveryFilters, DiscoveryJob, DiscoveryResponse, DiscoverySnapshot } from "./discovery";
import { safePostingUrl } from "./workspace";
import "./studio-workflow.css";
import "./discovery.css";

type Props = { userId: string; profile: BetaProfile; snapshot: DiscoverySnapshot | null; onSnapshot: (snapshot: DiscoverySnapshot) => void; onReviewResume: () => void; preferences: JobPreferences; savedJobs: BetaJob[]; onSaved: () => Promise<void>; onOpenStudio: (job: BetaJob) => void; onEditPreferences: () => void };
export function DiscoveryPanel({ userId, profile, snapshot, onSnapshot, onReviewResume, preferences, savedJobs, onSaved, onOpenStudio, onEditPreferences }: Props) {
  const contextKey = discoveryContextKey(userId, profile, preferences);
  const [query, setQuery] = useState("");
  const [titles, setTitles] = useState("");
  const [locations, setLocations] = useState("");
  const [workplace, setWorkplace] = useState<DiscoveryFilters["workplace_type"]>("any");
  const [result, setResult] = useState<DiscoveryResponse | null>(() => reusableDiscovery(snapshot, contextKey));
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retryAfter, setRetryAfter] = useState<number | null>(null);
  const [saved, setSaved] = useState(new Map<string, BetaJob>());
  const [uncertainSaves, setUncertainSaves] = useState(new Set<string>());
  const controller = useRef(new AbortController());
  const running = useRef(false);
  useEffect(() => { const request = new AbortController(); controller.current = request; return () => request.abort(); }, []);
  // The parent remounts on context changes. Defer a tick so StrictMode's probe
  // cleanup cancels before issuing a second network search. No AI or saving here.
  useEffect(() => {
    if (reusableDiscovery(snapshot, contextKey) || !preferences.target_titles.length) return;
    const timer = window.setTimeout(() => { void search(); }, 0);
    return () => window.clearTimeout(timer);
  }, []);
  const findSaved = (job: DiscoveryJob) => saved.get(job.source_id) ?? savedJobs.find(row => safePostingUrl(row.source_url) === safePostingUrl(job.source_url));
  const search = async () => {
    if (running.current) return;
    setError(""); setNotice(""); setRetryAfter(null);
    let body;
    try { body = discoveryRequest(query, titles, locations, workplace); } catch (cause) { setError((cause as Error).message); return; }
    running.current = true; setBusy(true); setResult(null);
    const signal = controller.current.signal;
    try {
      const data = checkedDiscoveryResponse(await mobileRequest(userId, "/discovery/search", { method: "POST", body, signal, timeoutMs: 30000 }));
      if (!signal.aborted) {
        setResult(data);
        if (!query.trim() && !titles.trim() && !locations.trim() && workplace === "any") onSnapshot({ key: contextKey, result: data, receivedAt: Date.now() });
      }
    } catch (cause) {
      if (signal.aborted) return;
      if (cause instanceof MobileApiError && cause.discoveryResult) {
        try { setResult(checkedDiscoveryResponse(cause.discoveryResult)); } catch { setError("Discovery sources were unavailable and their status could not be read. No role was saved."); }
      } else if (cause instanceof MobileApiError && [404, 405].includes(cause.status)) {
        setError("The server has not been updated for job discovery yet. Your resume and saved roles are unchanged. Contact support; you can still add a job link manually.");
      } else setError(cause instanceof Error ? cause.message : "Discovery is unavailable. No role was saved.");
      if (cause instanceof MobileApiError) setRetryAfter(cause.retryAfter ?? null);
    } finally { running.current = false; if (!signal.aborted) setBusy(false); }
  };
  const save = async (job: DiscoveryJob) => {
    if (running.current || findSaved(job) || uncertainSaves.has(job.source_id)) return;
    const signal = controller.current.signal;
    running.current = true; setSaving(job.source_id); setError(""); setNotice("");
    let confirmedSave = false;
    try {
      const item = await mobileRequest<BetaJob>(userId, "/jobs", { method: "POST", body: discoverySavePayload(job), signal });
      if (signal.aborted) return;
      if (!item.id || item.id === job.source_id) throw new Error("The service did not return a saved job ID. Refresh saved roles before retrying.");
      setSaved(current => new Map(current).set(job.source_id, item)); confirmedSave = true;
      setNotice(item.duplicate ? "This posting was already saved. Open the saved role and review its current description before preparing." : "Role saved by your request. Eligibility still needs a job-specific review. Open the saved role to continue; no AI or application was started.");
      await onSaved();
    } catch (cause) {
      if (!signal.aborted) {
        if (!confirmedSave && (!(cause instanceof MobileApiError) || cause.uncertain || cause.status >= 500)) setUncertainSaves(current => new Set(current).add(job.source_id));
        setError(confirmedSave ? "The role was saved, but the saved list could not refresh. Use Open saved role; do not save it again." : cause instanceof Error ? cause.message : "The role could not be saved. Check saved roles before retrying.");
      }
    } finally { running.current = false; if (!signal.aborted) setSaving(null); }
  };
  return <section className="workflow discovery-panel" aria-labelledby="discovery-title">
    <div className="workflow-card discovery-controls">
      <h2 id="discovery-title">Your recommendations</h2>
      <p className="discovery-lede">Private board check · no AI credits · nothing applied.</p>
      {!preferences.target_titles.length && <div className="beta-notice"><p>Tell us at least one role you want so we can start your search.</p><button className="beta-primary" onClick={onEditPreferences}>Choose target roles</button></div>}
      {!profile.career_text?.trim() && <div className="discovery-next-step"><h3>Make these results more personal</h3><p>Review confirmed resume facts to prioritize relevant roles. Until then, results use saved preferences only.</p><button className="beta-secondary" onClick={onReviewResume}>Review resume facts</button></div>}
      <details className="discovery-preferences"><summary>Saved preferences applied automatically</summary><dl><div><dt>Target titles</dt><dd>{preferences.target_titles.join(", ") || "No saved title constraint"}</dd></div><div><dt>Locations / regions</dt><dd>{[...preferences.preferred_locations, ...preferences.preferred_regions].join(", ") || "No saved location constraint"}</dd></div><div><dt>Workplace</dt><dd>{preferences.remote_preference.replace(/_/g, " ")}</dd></div><div><dt>Sponsorship needed</dt><dd>{preferences.sponsorship_required ? "Yes — provisional warnings only" : "Not recorded as required"}</dd></div></dl><button type="button" className="beta-text-button" disabled={busy || Boolean(saving)} onClick={onEditPreferences}>Edit saved preferences</button></details>
      <form onSubmit={event => { event.preventDefault(); void search(); }}><fieldset className="workflow-fieldset" disabled={busy || Boolean(saving)}>
        <details><summary>Refine search</summary>
        <p>Empty fields use your saved preferences. Additional filters only narrow them; edit your preferences to change profession or location.</p>
        <label>Posting keywords<input maxLength={160} value={query} onChange={event => { setQuery(event.target.value); setResult(null); }} placeholder="Optional words from the posting" /></label>
        <div className="discovery-filter-grid"><label>Additional title filters<input value={titles} onChange={event => { setTitles(event.target.value); setResult(null); }} placeholder="Optional, comma-separated" /><small>Up to 10 titles; any profession.</small></label><label>Additional location filters<input value={locations} onChange={event => { setLocations(event.target.value); setResult(null); }} placeholder="Optional cities, countries or regions" /><small>Up to 10 terms; geography may need review.</small></label><label>Additional workplace filter<select value={workplace} onChange={event => { setWorkplace(event.target.value as DiscoveryFilters["workplace_type"]); setResult(null); }}><option value="any">Use saved preference (no extra filter)</option><option value="remote">Remote signal required</option><option value="hybrid">Hybrid</option><option value="onsite">On-site</option></select></label></div>
        <button className="beta-secondary" type="button" onClick={() => { setQuery(""); setTitles(""); setLocations(""); setWorkplace("any"); setResult(null); setError(""); }}>Clear additional filters</button></details>
        <div className="workflow-actions"><button className="beta-primary" type="submit" disabled={!preferences.target_titles.length}>{busy ? "Finding relevant roles…" : "Refresh recommendations"}</button></div>
      </fieldset></form>
    </div>
    {busy && <p role="status">Checking boards…</p>}
    {error && <p className="beta-error" role="alert">{error}</p>}{retryAfter && <p className="beta-notice">The service asked you to wait {retryAfter} seconds before another search. No automatic retry will run.</p>}
    {notice && <p className="beta-notice" role="status">{notice}</p>}
    {result && <div className="workflow-stack discovery-results">
      {!!result.warnings?.length && <section className="workflow-card discovery-warnings-compact" aria-label="Discovery coverage and geography warnings"><h2>Search limitations to review</h2><ul>{result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></section>}
      {result.status === "unavailable" ? <p className="beta-error" role="alert">All configured discovery sources failed. This is not a successful search with no matches. Review source status below and search again later.</p>
        : <div className="workflow-card discovery-results-chrome">
          <h2>{result.results.length ? "Roles to explore" : "No matching roles in the fetched sample"}</h2>
          <p className="discovery-summary">{result.returned_count} of {result.matched_count} matches · {result.ranking === "profile_rules_v1" ? "ordered by explained profile relevance (not a hiring score)" : "filtered by saved preferences"} · {date(result.searched_at)}</p>
          {(result.partial || result.truncated || result.status === "partial") && <p className="beta-notice discovery-compact-notice">Partial coverage: some boards failed, omitted records, or hit a limit.</p>}
          <details className="discovery-about-results">
            <summary>About these results</summary>
            <p>Coverage is limited to the {result.sources.length} employer boards below—not the whole job market. Results stay unsaved until you choose a role.</p>
            <p className="discovery-provisional">{PROVISIONAL_NOTE} Board names are the employer&apos;s board identifier, not a verified company name. Preference match is not proof of experience.</p>
          </details>
          {!result.results.length && <><p>No suitable roles were found within these sources and your constraints. We have not changed your location or work preferences.</p><button className="beta-secondary" onClick={onEditPreferences}>Review search preferences</button></>}
        </div>}
      <details className="workflow-card discovery-sources" open={result.status !== "ok"}><summary>Source coverage ({result.sources.length} boards)</summary><ul>{result.sources.map(source => <li key={source.source}><h3>{source.source} <span className="discovery-chip">{source.status === "error" ? "Unavailable" : source.status === "partial" ? "Partial" : "Checked"}</span></h3><p>{source.cached ? "Cached snapshot/status" : "Checked for this search"}. Retrieved {date(source.fetched_at)}; checked {date(source.checked_at)}.</p><p>{source.received_count} received · {source.returned_count} usable postings · {source.dropped_count} malformed omitted · {source.unlisted_count} unlisted omitted · {source.duplicate_count} duplicates omitted.</p>{source.truncated && <p>Source sample or posting text was truncated.</p>}{source.error_code && <p>Source issue: {source.error_code}.{source.retry_after ? ` Wait at least ${source.retry_after} seconds before trying this source again.` : ""}</p>}</li>)}</ul></details>
      {result.results.map(job => {
        const savedJob = findSaved(job);
        const fit = strength(job);
        const gaps = job.relevance ? shownGaps(job.relevance.gaps) : [];
        return <article className="workflow-card discovery-result" key={job.source_id} aria-label={`${job.title} at ${job.company_name}`}>
          <div className="discovery-result-heading">
            {fit && <span className={`discovery-chip discovery-strength discovery-strength-${fit.level}`}>{fit.label}</span>}
            <h3>{job.title}</h3>
            <p className="discovery-meta">{job.company_name} · {job.location_text || "Location not supplied"}{job.workplace_type === "unknown" ? "" : ` · ${job.workplace_type}`}</p>
            {fitLine(job) && <p className="discovery-fit-line">{fitLine(job)}</p>}
          </div>
          <div className="workflow-actions discovery-result-actions">
            {savedJob
              ? <button className="beta-primary" disabled={busy || Boolean(saving)} onClick={() => onOpenStudio(savedJob)}>Open saved role</button>
              : <button className="beta-primary" disabled={busy || Boolean(saving) || uncertainSaves.has(job.source_id)} onClick={() => void save(job)}>{saving === job.source_id ? "Saving role…" : "Save role"}</button>}
            <a className="discovery-source-link" href={safePostingUrl(job.source_url)!} target="_blank" rel="noreferrer">View source ↗</a>
          </div>
          {job.relevance && <details className="discovery-why"><summary>Why this?</summary><div className="discovery-fit"><h4>Why this role appeared</h4><ul>{job.relevance.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>{gaps.length > 0 && <><h4>Gaps to review</h4><ul>{gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></>}</div></details>}
          <details className="discovery-more"><summary>Review posting and provisional reasons</summary><p className="discovery-provenance">Source: {job.source}. Retrieved {date(job.fetched_at)}{job.source_published_at ? ` · Source publication: ${date(job.source_published_at)}` : ""}{job.source_updated_at ? ` · Source update: ${date(job.source_updated_at)}` : ""}. A posting may close after retrieval.</p><p className="discovery-description">{job.description}</p><h4>Why it appeared</h4><ul>{job.match_reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul><h4>Review before applying</h4><ul>{job.eligibility.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></details>
          {job.content_truncated && <p className="beta-notice discovery-compact-notice">Posting text is truncated. Complete it in Studio before preparation.</p>}
          {uncertainSaves.has(job.source_id) && !savedJob && <p className="beta-notice discovery-compact-notice">Save outcome is uncertain. Check Saved roles before retrying.</p>}
          {savedJob && <p className="discovery-saved-note">Saved privately. Opening Studio does not start AI or submit an application.</p>}
        </article>;
      })}
    </div>}
  </section>;
}
function date(value: string | null | undefined) { if (!value) return "not available"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "not available" : parsed.toLocaleString(); }

/** Describes the overlap the local rules actually measured. Never a hiring
 * prediction, an eligibility claim, or an AI score: the wording stays
 * descriptive ("title + experience overlap") so a chip cannot be read as
 * "you are qualified". Absent when no confirmed career evidence was used. */
const STRENGTHS = [
  { at: 60, level: "strong", label: "Strong title + experience overlap" },
  { at: 30, level: "partial", label: "Partial overlap" },
  { at: 1, level: "weak", label: "Weak overlap" },
] as const;
function strength(job: DiscoveryJob) {
  if (!job.relevance) return null;
  return STRENGTHS.find(item => job.relevance!.score >= item.at) ?? null;
}

/** One scannable fit line when no strength chip is shown. Strength chips
 * already convey overlap; otherwise surface a short preference match reason. */
function fitLine(job: DiscoveryJob): string | null {
  if (strength(job)) return null;
  if (job.match_reasons[0]) return clip(job.match_reasons[0], 110);
  if (job.relevance?.reasons[0]) return clip(job.relevance.reasons[0], 110);
  return null;
}
function clip(value: string, max: number) {
  const text = value.trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}

/** Shown once for the whole page instead of repeated under all 20 cards. */
export const PROVISIONAL_NOTE = "Eligibility and qualifications remain provisional and are not independently verified.";
function shownGaps(gaps: string[]) { return gaps.filter(gap => gap !== PROVISIONAL_NOTE); }
