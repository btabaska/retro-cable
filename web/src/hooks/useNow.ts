import { useEffect, useState } from "react";

/** Re-render every `ms` and return Date.now(). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => {
      setNow(Date.now());
    }, ms);
    return () => {
      window.clearInterval(id);
    };
  }, [ms]);
  return now;
}
