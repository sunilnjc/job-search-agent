export type WorkspaceIconName = "today" | "discover" | "studio" | "tracker" | "profile" | "arrow";

export function WorkspaceIcon({ name }: { name: WorkspaceIconName }) {
  const paths: Record<WorkspaceIconName, React.ReactNode> = {
    today: <><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /></>,
    discover: <><circle cx="12" cy="12" r="9" /><path d="m16 8-2.5 5.5L8 16l2.5-5.5Z" /></>,
    studio: <><rect x="3" y="6" width="15" height="14" rx="3" /><path d="M7 3h11a3 3 0 0 1 3 3v10M10.5 10v6m-3-3h6" /></>,
    tracker: <><path d="M4 5h16v14H4zM4 14h4l2 3h4l2-3h4M8 9h8" /></>,
    profile: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="9" r="3" /><path d="M6 19c1-6 11-6 12 0" /></>,
    arrow: <path d="M5 19 19 5M6 5h13v13" />,
  };
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name]}</svg>;
}
