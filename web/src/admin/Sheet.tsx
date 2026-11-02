import { type ReactNode, useEffect, useRef } from "react";

interface SheetProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** A small centered dialog instead of a full-height bottom sheet. */
  compact?: boolean;
}

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** A modal bottom sheet: traps focus, closes on Esc, and returns focus to the opener. */
export function Sheet({ title, onClose, children, compact = false }: SheetProps) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const el = panel.current;
    const first = el?.querySelector<HTMLElement>("[autofocus], input, " + FOCUSABLE);
    (first ?? el)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
      } else if (event.key === "Tab" && el) {
        const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((i) => !i.hasAttribute("disabled"));
        const head = items[0];
        const tail = items[items.length - 1];
        if (!head || !tail) return;
        if (event.shiftKey && document.activeElement === head) {
          event.preventDefault();
          tail.focus();
        } else if (!event.shiftKey && document.activeElement === tail) {
          event.preventDefault();
          head.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, []);

  return (
    <div className="adm-scrim" onClick={onClose}>
      <div
        ref={panel}
        className={compact ? "adm-dialog" : "adm-sheet"}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onClick={(e) => {
          e.stopPropagation();
        }}
      >
        {children}
      </div>
    </div>
  );
}
