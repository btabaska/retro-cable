import { useEffect, useRef } from "react";

/** Listen for keydown on the window while mounted; the latest handler always runs. */
export function useKeys(handler: (event: KeyboardEvent) => void): void {
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler;
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.repeat && event.key === "Enter") return;
      ref.current(event);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);
}
