import type { WeeklyReport } from "./api";

/** "54 min" or "6 h 12 min" */
export function duration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes} min`;
}

/** "Week of Nov 8, 2026" from "2026-11-08". */
export function reportWeek(weekOf: string): string {
  const d = new Date(`${weekOf}T12:00:00Z`);
  return `Week of ${d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })}`;
}

/** "54 min watched (last week 15 min)" */
export function viewingLabel(r: WeeklyReport): string {
  const v = r.viewing;
  if (v.seconds === 0) return "Nothing watched";
  const last = v.last_week_seconds ? `last week ${duration(v.last_week_seconds)}` : "nothing last week";
  return `${duration(v.seconds)} watched (${last})`;
}

/** "6,301 episodes (+412) of 140 shows, 79 movies" */
export function libraryLabel(r: WeeklyReport): string {
  const l = r.library;
  const added = l.episodes_added ? ` (${l.episodes_added > 0 ? "+" : ""}${l.episodes_added.toLocaleString("en-US")})` : "";
  return `${l.episodes.toLocaleString("en-US")} episodes${added} of ${l.shows} shows, ${l.movies} movies`;
}

/** "3,028 files left, about 51 h; 3 failed" */
export function conversionLabel(r: WeeklyReport): string | null {
  const c = r.conversion;
  if (!c) return null;
  if (c.left <= 0) return `All ${c.total.toLocaleString("en-US")} files converted`;
  const eta = c.hours_left != null ? `, about ${Math.round(c.hours_left)} h` : "";
  const failed = c.failed ? `; ${c.failed} failed` : "";
  return `${c.left.toLocaleString("en-US")} files left${eta}${failed}`;
}

/** "Restore test passed", or why the backups need a look. */
export function backupLabel(r: WeeklyReport): string {
  const b = r.backups;
  if (!b.last_backup_at) return "No backup yet";
  if (b.ok) return "Last night's backup is on the NAS. Restore test passed";
  if (b.restore_ok === false) return `Restore test FAILED: ${b.restore_problems.slice(0, 2).join("; ") || "see the logs"}`;
  return "No backup in the last 36 hours";
}
