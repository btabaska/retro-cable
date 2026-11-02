import type { MediaProgress, MediaShowProgress } from "./api";

/** How often the Media tab asks for progress. */
export const POLL_MS = 10_000;
/** Files that must be done before the ETA is shown ("Estimating…" until then). */
export const ETA_AFTER_FILES = 10;
/** The rolling window the ETA averages over. */
export const ETA_WINDOW = 20;
const TEN_MINUTES = 600;

/** Seconds left: the average of the last 20 files times the files left, rounded to 10 minutes;
 * null while estimating. */
export function etaSeconds(p: MediaProgress): number | null {
  const recent = p.recent_seconds.slice(-ETA_WINDOW);
  if (p.done < ETA_AFTER_FILES || recent.length === 0) return null;
  const average = recent.reduce((a, b) => a + b, 0) / recent.length;
  const left = Math.max(0, p.total - p.done) * average;
  return Math.round(left / TEN_MINUTES) * TEN_MINUTES;
}

/** "about 6 h 10 m left", "about 40 m left", "Estimating…". */
export function etaLine(p: MediaProgress): string {
  const eta = etaSeconds(p);
  if (eta === null) return "Estimating…";
  if (p.done >= p.total) return "done";
  const h = Math.floor(eta / 3600);
  const m = Math.round((eta % 3600) / 60);
  if (h === 0) return m === 0 ? "a few minutes left" : `about ${m} m left`;
  return `about ${h} h ${m} m left`;
}

export function percent(p: MediaProgress): number {
  return p.total === 0 ? 0 : Math.floor((p.done / p.total) * 100);
}

/** "Encoding on GPU", "Encoding on CPU (GPU busy)", "Encoding on CPU", "Paused". */
export function encoderLine(p: MediaProgress): string {
  if (p.encoder === "paused") return "Paused";
  if (p.encoder === "gpu") return "Encoding on GPU";
  return p.gpu_busy ? "Encoding on CPU (GPU busy)" : "Encoding on CPU";
}

const ORDER: Record<MediaShowProgress["state"], number> = { converting: 0, queued: 1, done: 2 };

/** In progress first, then queued, then done; the server's order (the conversion order)
 * within each group. */
export function sortShows(shows: MediaShowProgress[]): MediaShowProgress[] {
  return shows
    .map((s, i) => ({ s, i }))
    .sort((a, b) => ORDER[a.s.state] - ORDER[b.s.state] || a.i - b.i)
    .map(({ s }) => s);
}
