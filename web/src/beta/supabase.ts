import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL?.trim();
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY?.trim();

// An explicit switch prevents the new beta from changing the founder's existing
// personal deployment just because shared environment values are present.
export const betaMode = import.meta.env.VITE_APP_MODE === "beta"
  || window.location.pathname === "/beta"
  || window.location.pathname.startsWith("/beta/");

/**
 * The existing founder dashboard deliberately remains the default.  This beta
 * client exists only when both public Supabase values are configured at build
 * time; the service-role key is never present in the browser.
 */
export const betaEnabled = Boolean(url && anonKey);

export const supabase: SupabaseClient | null = betaEnabled
  ? createClient(url!, anonKey!, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null;

export function requireSupabase(): SupabaseClient {
  if (!supabase) {
    throw new Error("The multi-user beta is not configured yet.");
  }
  return supabase;
}
