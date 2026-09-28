import test from "node:test";
import assert from "node:assert/strict";
import { sponsorshipExcerpts, usSponsorshipPreferences } from "../src/beta/sponsorshipEvidence.ts";

test("US preset preserves personal facts and strict preferences without inventing visa status", () => {
  const original = { preferredLocations: "London", preferredRegions: "Europe", remoteCountryCodes: "GB",
    sponsorshipRequired: false, sponsorshipPolicy: "require_explicit", remoteCountryPolicy: "require_explicit",
    baseLocation: "Dubai", workAuthorizationNotes: "Ask me", targetTitles: "Engineer", remotePreference: "remote_only" };
  const result = usSponsorshipPreferences(original);
  assert.equal(result.preferredLocations, "United States");
  assert.equal(result.remoteCountryCodes, "US");
  assert.equal(result.preferredRegions, "");
  assert.equal(result.sponsorshipRequired, true);
  for (const key of ["baseLocation", "workAuthorizationNotes", "targetTitles", "remotePreference", "sponsorshipPolicy", "remoteCountryPolicy"]) assert.equal(result[key], original[key]);
  assert.equal(original.preferredLocations, "London");
});
test("shows opposing and conditional statements verbatim without asserting support", () => {
  const text = "Visa sponsorship may be available. We do not sponsor new visas. H-1B transfers considered.";
  assert.deepEqual(sponsorshipExcerpts(text), text.split(/(?<=[.!?])\s+/));
});
test("authorization text is evidence to review, not a sponsorship refusal", () => {
  assert.deepEqual(sponsorshipExcerpts("Must be authorized to work in the US."), ["Must be authorized to work in the US."]);
  assert.deepEqual(sponsorshipExcerpts("Remote engineer. Java and Kafka."), []);
});
test("handles unicode H1B, deduplicates and bounds evidence", () => {
  assert.equal(sponsorshipExcerpts("H‑1B support\nH‑1B support").length, 1);
  assert.equal(sponsorshipExcerpts(Array.from({length:10}, (_,i) => `Sponsorship condition ${i}`).join("\n")).length, 5);
  assert.match(sponsorshipExcerpts("Sponsorship " + "a".repeat(900))[0], /excerpt shortened/);
});
