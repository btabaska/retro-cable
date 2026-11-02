import type { GuideSlot } from "../api/client";

export const WINDOW_MS = 90 * 60_000;
const HALF_HOUR = 30 * 60_000;

export interface GuideCell {
  slot: GuideSlot;
  start: number;
  /** Where the next program starts, so the bumper between them belongs to this cell. */
  end: number;
  /** Fraction of the window from its left edge. */
  left: number;
  width: number;
  clippedStart: boolean;
}

/** Cells for one channel row: programs at their true length, bumpers folded in. */
export function layoutRow(slots: GuideSlot[], windowStart: number, windowEnd: number): GuideCell[] {
  const programs = slots.filter((s) => s.kind !== "bumper");
  const span = windowEnd - windowStart;
  const cells: GuideCell[] = [];
  programs.forEach((slot, i) => {
    const start = Date.parse(slot.start);
    const following = programs[i + 1];
    const end = following ? Date.parse(following.start) : Date.parse(slot.end);
    if (end <= windowStart || start >= windowEnd) return;
    const from = Math.max(start, windowStart);
    const to = Math.min(end, windowEnd);
    cells.push({
      slot,
      start,
      end,
      left: (from - windowStart) / span,
      width: (to - from) / span,
      clippedStart: start < windowStart,
    });
  });
  return cells;
}

/** The cell airing at `t`, or the nearest one when none covers it. */
export function cellIndexAt(cells: GuideCell[], t: number): number {
  const hit = cells.findIndex((c) => c.start <= t && t < c.end);
  if (hit >= 0) return hit;
  const first = cells[0];
  if (!first || t < first.start) return 0;
  return cells.length - 1;
}

/** Guide windows start on the half hour. */
export function windowStartFor(t: number): number {
  return Math.floor(t / HALF_HOUR) * HALF_HOUR;
}

export const HALF_HOUR_MS = HALF_HOUR;
