/** Approved Unfold identity. Keep the wordmark as accessible, scalable text. */
export function BrandIdentity({ subtitle }: { subtitle?: string }) {
  return <span className="pursuit-identity">
    <img className="pursuit-identity-mark" src="/brand/unfold-mark-v1.png" width="44" height="44" alt="" />
    <span className="pursuit-identity-copy"><span className="pursuit-identity-name">the job pursuit</span>{subtitle && <small>{subtitle}</small>}</span>
  </span>;
}
