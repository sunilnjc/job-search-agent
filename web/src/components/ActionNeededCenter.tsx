import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ApplicationQuestion } from "../types";

type Props = { onClose: () => void };

export function ActionNeededCenter({ onClose }: Props) {
  const [items, setItems] = useState<ApplicationQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true); setError(null);
    try { setItems(await api.listApplicationQuestions()); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not load pending questions."); }
    finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, []);

  const approve = async (item: ApplicationQuestion) => {
    const answer = answers[item.attempt_id]?.trim();
    if (!answer) return;
    setSaving(item.attempt_id); setError(null);
    try {
      await api.approveApplicationQuestion(item.question, answer, item.job_id);
      setItems((current) => current.filter((candidate) => candidate.attempt_id !== item.attempt_id));
    } catch (err) { setError(err instanceof Error ? err.message : "Could not save this answer."); }
    finally { setSaving(null); }
  };

  return <div className="approval-overlay" role="dialog" aria-modal="true" aria-labelledby="approval-center-title">
    <section className="approval-center">
      <header><div><p className="approval-eyebrow">AUTOPILOT · ACTION NEEDED</p><h2 id="approval-center-title">One answer unlocks the next step</h2><p>The agent paused before submitting because this answer is not in your approved profile.</p></div><button className="approval-close" onClick={onClose} aria-label="Close">×</button></header>
      {loading && <p>Loading questions…</p>}
      {error && <p className="approval-error" role="alert">{error}</p>}
      {!loading && !error && items.length === 0 && <div className="approval-empty"><strong>Nothing is waiting for you.</strong><p>The agent will automatically continue supported applications using your saved factual answers.</p></div>}
      <div className="approval-list">
        {items.map((item) => <article className="approval-card" key={item.attempt_id}>
          <p className="approval-role">{item.title} · {item.company}</p>
          <label>Employer question<textarea value={item.question} readOnly rows={3} /></label>
          <label>Your approved answer<input value={answers[item.attempt_id] ?? ""} onChange={(event) => setAnswers((current) => ({ ...current, [item.attempt_id]: event.target.value }))} placeholder="Enter the truthful answer or exact option" /></label>
          <p className="approval-note">This is saved only in your private local profile. Once saved, the agent retries this application automatically.</p>
          <button className="approval-submit" disabled={saving === item.attempt_id || !answers[item.attempt_id]?.trim()} onClick={() => void approve(item)}>{saving === item.attempt_id ? "Saving and retrying…" : "Approve and retry automatically"}</button>
        </article>)}
      </div>
    </section>
  </div>;
}
