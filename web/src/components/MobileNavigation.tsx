type MobileView = "board" | "applications" | "excluded";

interface Props {
  view: MobileView;
  onChange: (view: MobileView) => void;
}

const ITEMS: { view: MobileView; label: string; icon: string }[] = [
  { view: "board", label: "Today", icon: "⌂" },
  { view: "applications", label: "Applications", icon: "✓" },
  { view: "excluded", label: "Excluded", icon: "⊘" },
];

/** Fixed, thumb-friendly navigation used only on phone-sized screens. */
export function MobileNavigation({ view, onChange }: Props) {
  return (
    <nav className="mobile-navigation" aria-label="Primary navigation">
      {ITEMS.map((item) => (
        <button
          key={item.view}
          className={view === item.view ? "mobile-nav-item active" : "mobile-nav-item"}
          onClick={() => onChange(item.view)}
          aria-current={view === item.view ? "page" : undefined}
        >
          <span className="mobile-nav-icon" aria-hidden="true">{item.icon}</span>
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
