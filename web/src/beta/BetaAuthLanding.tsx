import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { requireSupabase } from "./supabase";
import { BrandIdentity } from "./BrandIdentity";
import { trustPageEnabled } from "./trustPage";

type AuthMode = "signin" | "signup";

type BetaAuthLandingProps = {
  notice: string | null;
  error: string | null;
  onNotice: (value: string | null) => void;
  onError: (value: string | null) => void;
  privacyIntent?: boolean;
  onPrivacyIntentChange?: (value: boolean) => void;
};

/**
 * The beta intentionally uses one passwordless email flow for both account
 * creation and returning sign-in. The two modes are a clear user-facing
 * explanation, not two different sources of truth or password stores.
 * Privacy intent is a separate callout, not a silent override of Sign in.
 */
export function BetaAuthLanding({ notice, error, onNotice, onError, privacyIntent = false, onPrivacyIntentChange }: BetaAuthLandingProps) {
  const [mode, setMode] = useState<AuthMode>(privacyIntent ? "signin" : "signup");
  const [email, setEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [retryAfter, setRetryAfter] = useState(0);

  useEffect(() => {
    if (privacyIntent) setMode("signin");
  }, [privacyIntent]);

  useEffect(() => {
    if (retryAfter <= 0) return;
    const timer = window.setTimeout(() => setRetryAfter((seconds) => seconds - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [retryAfter]);

  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    if (sending || retryAfter > 0 || !email.trim()) return;
    setSending(true);
    onError(null);
    onNotice(null);
    try {
      const { error: signInError } = await requireSupabase().auth.signInWithOtp({
        email: email.trim(),
        options: { emailRedirectTo: new URL("/beta", window.location.origin).toString(), ...(privacyIntent ? { shouldCreateUser: false } : {}) },
      });
      if (signInError) {
        if (signInError.status === 429) {
          setRetryAfter(60);
          onError("Email delivery is temporarily rate-limited. Please wait before trying again; the sender limit may last longer than a minute.");
        } else onError("We could not send a sign-in link. Check your email address and try again.");
        return;
      }
      setSentTo(email.trim().toLowerCase());
      setRetryAfter(60);
      onNotice(`A secure sign-in link was sent to ${email.trim()}.`);
    } catch {
      onError("We could not reach the sign-in service. Check your connection and try again.");
    } finally {
      setSending(false);
    }
  };

  const switchMode = (next: AuthMode) => {
    onPrivacyIntentChange?.(false);
    setMode(next);
    setSentTo(null);
    onNotice(null);
    onError(null);
  };

  const enterPrivacy = () => {
    setMode("signin");
    setSentTo(null);
    onPrivacyIntentChange?.(true);
    onNotice(null);
    onError(null);
  };

  return (
    <main className="beta-auth-page">
      <section className="beta-auth-brand" aria-label="The Job Pursuit overview">
        <a className="beta-auth-wordmark" href="/beta" aria-label="The Job Pursuit home"><BrandIdentity /></a>
        <div className="beta-auth-brand-copy">
          <p className="beta-eyebrow">A more deliberate job search</p>
          <h1>Move from searching to pursuing.</h1>
          <p>A private place for your career profile, saved opportunities, source resumes, and application history.</p>
        </div>
        <ul className="beta-auth-benefits">
          <li><span>01</span> Keep the roles you want to pursue together</li>
          <li><span>02</span> Store your source resumes privately</li>
          <li><span>03</span> Track preparation, applications and next steps</li>
        </ul>
        <p className="beta-auth-brand-footnote">Private beta · Built for thoughtful, international job searches.{trustPageEnabled ? <> · <a href="/beta/trust">Trust and safety</a></> : null}</p>
      </section>

      <section className="beta-auth-panel" aria-labelledby="beta-auth-title">
        <a className="beta-auth-mobile-wordmark" href="/beta" aria-label="The Job Pursuit home"><BrandIdentity /></a>
        <div className="beta-auth-mode-tabs" role="group" aria-label="Account action">
          <button type="button" disabled={sending} aria-pressed={mode === "signup" && !privacyIntent} className={mode === "signup" && !privacyIntent ? "is-active" : ""} onClick={() => switchMode("signup")}>Create account</button>
          <button type="button" disabled={sending} aria-pressed={mode === "signin" && !privacyIntent} className={mode === "signin" && !privacyIntent ? "is-active" : ""} onClick={() => switchMode("signin")}>Sign in</button>
        </div>

        {privacyIntent && <div className="beta-auth-privacy-callout" role="status">
          <p><strong>Privacy controls</strong> — export or erase an existing account. This is not account creation. Paid workspace access is not required.</p>
          <button type="button" className="beta-auth-link-button" disabled={sending} onClick={() => onPrivacyIntentChange?.(false)}>Back to sign in</button>
        </div>}

        {!sentTo ? <>
          <header className="beta-auth-panel-heading">
            <h2 id="beta-auth-title">{privacyIntent ? "Sign in to manage account privacy." : mode === "signup" ? "Start with your career, not another form." : "Welcome back."}</h2>
            <p>{privacyIntent ? "Use the email on your existing Job Pursuit account. After you open the link, choose Account privacy in your profile. If the link opens a new tab, sign in there first." : mode === "signup" ? "Create your private workspace in a few focused steps." : "Use the email connected to your workspace."}</p>
          </header>
          <form className="beta-auth-form" onSubmit={(event) => void send(event)}>
            <label>Email address<input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" /></label>
            <button className="beta-auth-email-action" disabled={sending || retryAfter > 0}>{sending ? "Sending secure link…" : privacyIntent ? "Email a privacy sign-in link" : mode === "signup" ? "Create account with email" : "Continue with email"}</button>
          </form>
          <p className="beta-auth-no-password">No password to create or remember. We email a secure sign-in link.</p>
        </> : <div className="beta-auth-confirmation" role="status">
          <div className="beta-auth-confirmation-icon" aria-hidden="true">↗</div>
          <h2>Check your inbox</h2>
          <p>We sent a secure link to <strong>{sentTo}</strong>. Open it on this device and you will return directly to your workspace.</p>
          <div className="beta-auth-confirmation-actions">
            <button type="button" className="beta-auth-link-button" onClick={() => setSentTo(null)}>Use a different email</button>
            <button type="button" className="beta-auth-link-button" onClick={() => void send()} disabled={sending || retryAfter > 0}>{retryAfter > 0 ? `Resend available in ${retryAfter}s` : "Resend link"}</button>
          </div>
        </div>}

        <div className="beta-auth-security"><span aria-hidden="true">⌁</span><p>Your personal profile and documents remain private to your account. We never submit an application without your approval.</p></div>
        {!privacyIntent && <button type="button" className="beta-auth-link-button" disabled={sending} onClick={enterPrivacy}>Need export or erasure? Open privacy sign-in</button>}
        <p className="beta-auth-legal">By continuing you agree to receive a one-time sign-in email. {trustPageEnabled ? <a href="/beta/trust">Trust and privacy</a> : "Privacy export and erasure controls are available after sign-in under Your profile."} Terms for a public launch will be published separately.</p>
        {notice && <p className="beta-notice" role="status">{notice}</p>}
        {error && <p className="beta-error" role="alert">{error}</p>}
      </section>
    </main>
  );
}
