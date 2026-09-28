import type { WizardFields } from "./onboardingDraft.ts";

/** Search preference only: never infer residence, visa status or work rights. */
export function usSponsorshipPreferences(fields: WizardFields): WizardFields {
  return { ...fields, preferredLocations: "United States", preferredRegions: "",
    sponsorshipRequired: true, remoteCountryCodes: "US" };
}

/** Verbatim excerpts, not a sponsorship classifier or a legal eligibility decision. */
export function sponsorshipExcerpts(description: string): string[] {
  return [...new Set(description.split(/\n+|(?<=[.!?])\s+/)
    .map(line => line.trim()).filter(line => /\b(?:h[‐‑–-]?1b|sponsor(?:ship|ing|ed|s)?|work authori[sz]ation|authori[sz]ed to work|immigration|work permit)\b/i.test(line)))]
    .slice(0, 5).map(line => line.length > 700 ? line.slice(0, 700) + "… [excerpt shortened]" : line);
}
