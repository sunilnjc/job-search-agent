/** Parse Supabase auth redirect failures from the current URL and strip them. */
export function consumeAuthRedirectError(
  search = typeof window !== "undefined" ? window.location.search : "",
  hash = typeof window !== "undefined" ? window.location.hash : "",
  pathname = typeof window !== "undefined" ? window.location.pathname : "/beta",
): string | null {
  const fromSearch = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const hashQuery = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : hash.startsWith("#") ? hash.slice(1) : hash;
  const fromHash = new URLSearchParams(hashQuery);
  const code = (fromSearch.get("error_code") || fromHash.get("error_code") || "").toLowerCase();
  const error = (fromSearch.get("error") || fromHash.get("error") || "").toLowerCase();
  const description = fromSearch.get("error_description") || fromHash.get("error_description") || "";
  const relevant = Boolean(code || error || /otp|magic.?link|expired|invalid/i.test(description));
  if (!relevant) return null;

  if (typeof window !== "undefined") {
    const cleaned = new URLSearchParams(fromSearch);
    for (const key of ["error", "error_code", "error_description"]) cleaned.delete(key);
    const nextSearch = cleaned.toString();
    const nextHash = hash.includes("?") ? hash.slice(0, hash.indexOf("?")) : (hash.startsWith("#access_token") || hash.startsWith("#error") ? "" : hash);
    const href = pathname + (nextSearch ? `?${nextSearch}` : "") + nextHash;
    const current = window.location.pathname + window.location.search + window.location.hash;
    if (href !== current) window.history.replaceState(window.history.state, "", href);
  }

  if (code === "otp_expired" || /expired/i.test(description) || error === "access_denied" && /otp|magic/i.test(description)) {
    return "That sign-in link is invalid or has expired. Request a new link below and open it on this device.";
  }
  if (error || code) {
    return "We could not complete sign-in from that email link. Request a new link below and try again.";
  }
  return null;
}
