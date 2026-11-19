import { useEffect, useRef, useState } from "react";
import { HEARTBEAT_MS, type SendWatch, type Watching, WatchReporter } from "../lib/watch";

/**
 * Reports what is being watched (M5): the program playing on the tuned channel, a heartbeat
 * every minute, a flush when the page is hidden and power-off when it goes away. It only
 * observes; tuning never waits on it.
 */
export function useWatchReporter(watching: Watching | null, send: SendWatch | undefined): void {
  const [reporter] = useState(() => (send ? new WatchReporter(send) : null));
  const latest = useRef(watching);
  useEffect(() => {
    latest.current = watching;
  });

  const key = watching ? `${watching.channel}|${watching.year}|${watching.slot.start}|${watching.slot.kind}` : "";
  useEffect(() => {
    reporter?.watch(latest.current);
  }, [reporter, key]);

  useEffect(() => {
    if (!reporter) return;
    const beat = window.setInterval(() => {
      reporter.heartbeat();
    }, HEARTBEAT_MS);
    const onVisibility = () => {
      if (document.visibilityState === "hidden") reporter.flush();
    };
    const onPageHide = () => {
      reporter.end("power_off", true);
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.clearInterval(beat);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [reporter]);
}
