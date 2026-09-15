import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

/** Native dialog provides focus containment, Escape dismissal and focus return. */
export function WorkspaceDialog({ children, className, label, labelledBy, onClose }: {
  children: ReactNode; className: string; label?: string; labelledBy?: string; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previousFocus = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
    };
  }, []);
  return <dialog ref={ref} className={`pursuit-dialog ${className}`} aria-label={label} aria-labelledby={labelledBy}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    {children}
  </dialog>;
}
