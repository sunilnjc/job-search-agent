export const trustPageEnabled = import.meta.env?.VITE_TRUST_PAGE_ENABLED === "true";

export function isTrustPath(pathname: string): boolean {
  return pathname === "/beta/trust" || pathname === "/beta/trust/" || pathname === "/trust" || pathname === "/trust/";
}
