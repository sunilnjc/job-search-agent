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
    <div className="workflow-card"><h2 id="discovery-title">Your recommendations</h2><p>We check selected employer boards when you open Discover. Your profile stays private; employers do not receive it. This search uses no AI credits and does not apply to any job.</p>
      {!preferences.target_titles.length && <div className="beta-notice"><p>Tell us at least one role you want so we can start your search.</p><button className="beta-primary" onClick={onEditPreferences}>Choose target roles</button></div>}
      {!profile.career_text?.trim() && <div className="discovery-next-step"><h3>Make these results more personal</h3><p>An uploaded resume is not yet confirmed career information. Review its extracted facts to help us prioritize relevant roles. Until then, results use your saved preferences only.</p><button className="beta-secondary" onClick={onReviewResume}>Review resume facts</button></div>}
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
    {busy && <p role="status">Checking the configured boards. No role is being saved; no AI is running.</p>}
    {error && <p className="beta-error" role="alert">{error}</p>}{retryAfter && <p className="beta-notice">The service asked you to wait {retryAfter} seconds before another search. No automatic retry will run.</p>}
    {notice && <p className="beta-notice" role="status">{notice}</p>}
    {result && <div className="workflow-stack discovery-results">
      {!!result.warnings?.length && <section className="workflow-card" aria-label="Discovery coverage and geography warnings"><h2>Search limitations to review</h2><ul>{result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></section>}
      {result.status === "unavailable" ? <p className="beta-error" role="alert">All configured discovery sources failed. This is not a successful search with no matches. Review source status below and search again later.</p>
        : <div className="workflow-card"><h2>{result.results.length ? "Roles to explore" : "No matching roles in the fetched sample"}</h2><p>{result.returned_count} shown from {result.matched_count} matching postings. {result.ranking === "profile_rules_v1" ? "Ordered by explained profile relevance, not an AI score or a hiring prediction." : "Public-board results filtered by your saved preferences. Confirmed career evidence was not used for this ordering."} Checked {date(result.searched_at)}.</p><p>Coverage is limited to the {result.sources.length} employer boards below—not the whole job market. Search results are not saved until you choose a role.</p>{(result.partial || result.truncated || result.status === "partial") && <p className="beta-notice">Partial coverage: one or more sources failed, omitted records or hit a limit. Results are not a complete view of those boards or the job market.</p>}{!result.results.length && <><p>No suitable roles were found within these sources and your constraints. We have not changed your location or work preferences.</p><button className="beta-secondary" onClick={onEditPreferences}>Review search preferences</button></>}</div>}
      <details className="workflow-card discovery-sources" open={result.status !== "ok"}><summary>Source coverage ({result.sources.length} boards)</summary><ul>{result.sources.map(source => <li key={source.source}><h3>{source.source} <span className="discovery-chip">{source.status === "error" ? "Unavailable" : source.status === "partial" ? "Partial" : "Checked"}</span></h3><p>{source.cached ? "Cached snapshot/status" : "Checked for this search"}. Retrieved {date(source.fetched_at)}; checked {date(source.checked_at)}.</p><p>{source.received_count} received · {source.returned_count} usable postings · {source.dropped_count} malformed omitted · {source.unlisted_count} unlisted omitted · {source.duplicate_count} duplicates omitted.</p>{source.truncated && <p>Source sample or posting text was truncated.</p>}{source.error_code && <p>Source issue: {source.error_code}.{source.retry_after ? ` Wait at least ${source.retry_after} seconds before trying this source again.` : ""}</p>}</li>)}</ul></details>
      {result.results.map(job => {
        const savedJob = findSaved(job);
        return <article className="workflow-card discovery-result" key={job.source_id} aria-label={`${job.title} at ${job.company_name}`}>
          <div className="discovery-result-heading"><span className="discovery-chip">Eligibility unknown · review required</span><h3>{job.title}</h3><p>{job.company_name_is_board_identifier ? `Board label: ${job.company_name} (not a verified employer name)` : job.company_name} · {job.location_text || "Location not supplied"} · {job.workplace_type === "unknown" ? "Workplace unknown" : job.workplace_type}</p></div>
          {job.relevance && <div className="discovery-fit"><h4>Why this role appeared</h4><ul>{job.relevance.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>{job.relevance.gaps.length > 0 && <><h4>Gaps to review</h4><ul>{job.relevance.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></>}</div>}
          <p className="discovery-provenance">Source: {job.source}. Retrieved {date(job.fetched_at)}{job.source_published_at ? ` · Source publication: ${date(job.source_published_at)}` : ""}{job.source_updated_at ? ` · Source update: ${date(job.source_updated_at)}` : ""}. A posting may close after retrieval.</p>
          <details><summary>Review posting and provisional reasons</summary><p className="discovery-description">{job.description}</p><h4>Why it appeared</h4><ul>{job.match_reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul><h4>Review before applying</h4><ul>{job.eligibility.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></details>
          {job.content_truncated && <p className="beta-notice">Posting text is truncated. Review the original and complete the description in Studio before preparation.</p>}
          <div className="workflow-actions"><a className="discovery-source-link" href={safePostingUrl(job.source_url)!} target="_blank" rel="noreferrer">View source posting ↗</a>{savedJob ? <button className="beta-secondary" disabled={busy || Boolean(saving)} onClick={() => onOpenStudio(savedJob)}>Open saved role</button> : <button className="beta-secondary" disabled={busy || Boolean(saving) || uncertainSaves.has(job.source_id)} onClick={() => void save(job)}>{saving === job.source_id ? "Saving role…" : "Save role"}</button>}</div>
          {uncertainSaves.has(job.source_id) && !savedJob && <p className="beta-notice">Save outcome is uncertain. Check Saved roles or refresh the workspace before attempting another save. This search did not automatically retry it.</p>}
          {savedJob && <p>Saved in your private workspace. Opening Studio does not start AI or submit an application.</p>}
        </article>;
      })}
    </div>}
  </section>;
}
function date(value: string | null | undefined) { if (!value) return "not available"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "not available" : parsed.toLocaleString(); }
