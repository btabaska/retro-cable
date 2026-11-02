import type { GuideSlot } from "../api/client";

export interface KickerPart {
  text: string;
  /** new = gold, plain = muted. Always text, never color alone. */
  tone: "new" | "plain";
}

/** What follows the start time in a guide cell: NEW · MOVIE. A holiday program is tagged by the
 * cell's left bar instead; its name is in the details panel and the banner (M2 D29). */
export function kicker(slot: GuideSlot): KickerPart[] {
  if (slot.kind !== "episode" && slot.kind !== "movie") return [];
  const parts: KickerPart[] = [];
  if (slot.new) parts.push({ text: "NEW", tone: "new" });
  if (slot.kind === "movie") parts.push({ text: "MOVIE", tone: "plain" });
  return parts;
}

/** "Sep 2004" from "2004-09-07". */
export function monthYear(isoDate: string): string {
  const d = new Date(`${isoDate.slice(0, 10)}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

/** 85 minutes -> "1h 25m". */
export function duration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

/** The line under a title: "Episode title · S10 E4", or "1998 · 1h 25m" for a movie. */
export function programLabel(slot: GuideSlot): string {
  if (slot.kind === "movie") {
    const parts = [];
    if (slot.air_date) parts.push(slot.air_date.slice(0, 4));
    parts.push(duration(slot.seconds));
    return parts.join(" · ");
  }
  const parts = [];
  if (slot.episode_title) parts.push(slot.episode_title);
  if (slot.season !== null && slot.episode !== null) parts.push(`S${slot.season} E${slot.episode}`);
  return parts.join(" · ");
}
