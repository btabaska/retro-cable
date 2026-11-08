import type { StationRun } from "./api";

type Change = NonNullable<StationRun["report"]["changes"]>[number];
type PowerHour = NonNullable<StationRun["report"]["power_hours"]>[number];

const STATUS: Record<StationRun["status"], string> = {
  applied: "Applied",
  proposed: "Proposed",
  dry_run: "Dry run",
  skipped: "No change",
  failed: "Failed",
};

export function statusLabel(run: StationRun): string {
  return STATUS[run.status];
}

/** "Week of Nov 8, 2026" from "2026-11-08". */
export function weekLabel(run: StationRun): string {
  const d = new Date(`${run.week_of}T12:00:00Z`);
  return `Week of ${d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })}`;
}

/** "8 PM" from "20:00". */
export function hourLabel(hhmm: string): string {
  const h = Number(hhmm.slice(0, 2));
  if (h === 0) return "midnight";
  if (h === 12) return "noon";
  return `${((h + 11) % 12) + 1} ${h < 12 ? "AM" : "PM"}`;
}

/** "4 h 28 min watched in 4 weeks" */
export function watchedLabel(run: StationRun): string {
  const minutes = Math.round(run.report.signals.total_seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  const time = h ? `${h} h ${m} min` : `${m} min`;
  return `${time} watched in ${run.report.weeks} week${run.report.weeks === 1 ? "" : "s"}`;
}

/** "The Simpsons, 8 PM part: ×1 → ×1.62" */
export function changeLabel(c: Change): string {
  const where = c.start ? `, ${hourLabel(c.start)} part` : "";
  return `${c.title || c.show}${where}: ×${c.before} → ×${c.after}`;
}

/** "Power hour 8–9 PM: The Simpsons, Futurama" */
export function powerLabel(p: PowerHour): string {
  const end = `${String(Number(p.start.slice(0, 2)) + p.hours).padStart(2, "0")}:00`;
  const names = (p.titles.length ? p.titles : p.shows).join(", ");
  return `Power hour ${hourLabel(p.start)} to ${hourLabel(end)}: ${names}`;
}
