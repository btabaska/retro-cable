import { useEffect, useState } from "react";

export const TOAST_MS = 10_000;

interface ToastProps {
  message: string;
  canUndo: boolean;
  onUndo: () => void;
  onDone: () => void;
}

/** "Saved Disney Channel  [Undo]": 10 s, paused while hovered or focused. */
export function Toast({ message, canUndo, onUndo, onDone }: ToastProps) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const id = window.setTimeout(onDone, TOAST_MS);
    return () => {
      window.clearTimeout(id);
    };
  }, [paused, onDone, message]);

  return (
    <div
      className="adm-toast"
      role="status"
      onMouseEnter={() => {
        setPaused(true);
      }}
      onMouseLeave={() => {
        setPaused(false);
      }}
      onFocus={() => {
        setPaused(true);
      }}
      onBlur={() => {
        setPaused(false);
      }}
    >
      <span>{message}</span>
      {canUndo && (
        <button type="button" className="adm-toast-undo" onClick={onUndo}>
          Undo
        </button>
      )}
    </div>
  );
}
