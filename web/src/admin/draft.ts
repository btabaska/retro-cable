import type { AdminChannelRow, ChannelDraft, CheckIssue, DraftShow, LibraryEntry } from "./api";

export const FIRST_YEAR = 1990;
export const LAST_YEAR = 2010;

const TYPE_LABEL: Record<ChannelDraft["type"], string> = {
  network: "Network",
  marathon: "Marathon",
  movie: "Movies",
};

export const typeLabel = (type: ChannelDraft["type"]) => TYPE_LABEL[type];

/** The lowest channel number no channel uses, from 2. */
export function nextFreeNumber(channels: AdminChannelRow[]): number {
  const used = new Set(channels.map((c) => c.number));
  let n = 2;
  while (used.has(n)) n += 1;
  return n;
}

export function emptyDraft(): ChannelDraft {
  return { number: 0, call_sign: "", name: "", type: "network", library: false, launch_year: null, shows: [] };
}

/** Save is enabled once a draft has a number, a name and one show; the server checks the rest. */
export function readyToSave(draft: ChannelDraft): boolean {
  return draft.number > 0 && draft.name.trim() !== "" && (draft.shows ?? []).length > 0;
}

/** "Network · 12 shows", "Library · 2 shows", "from 2001 · 5 shows". */
export function channelMeta(row: AdminChannelRow): string {
  const shows = `${row.shows} ${row.shows === 1 ? "show" : "shows"}`;
  if (row.library) return `Library · ${shows}`;
  if (row.launch && Number(row.launch.slice(0, 4)) > FIRST_YEAR) return `from ${row.launch.slice(0, 4)} · ${shows}`;
  return `${typeLabel(row.type)} · ${shows}`;
}

/** "from 2002", "2001–2006", "1995 only", or "every year". */
export function carriageLine(show: DraftShow): string {
  const from = show.from_year ?? null;
  const until = show.until_year ?? null;
  if (from && until) return from === until ? `${from} only` : `${from}–${until}`;
  if (from) return `from ${from}`;
  if (until) return `until ${until}`;
  return "every year";
}

export function yearsLine(entry: LibraryEntry): string {
  const { first_year: a, last_year: b } = entry;
  const count = entry.kind === "movie" ? "movie" : `${entry.episodes} ep`;
  if (a && b && a !== b) return `${a}–${b} · ${count}`;
  if (a) return `${a} · ${count}`;
  return count;
}

export function issuesFor(issues: CheckIssue[], field: string): CheckIssue[] {
  return issues.filter((i) => i.field === field);
}

/** The picker's two groups: shows no channel carries first, then the rest. */
export function groupLibrary(entries: LibraryEntry[]): { free: LibraryEntry[]; carried: LibraryEntry[] } {
  return {
    free: entries.filter((e) => e.channels.length === 0),
    carried: entries.filter((e) => e.channels.length > 0),
  };
}

export function carriedOn(entry: LibraryEntry): string {
  return entry.channels.map((c) => `on ${c.number} ${c.name}`).join(", ");
}

export function sameDraft(a: ChannelDraft, b: ChannelDraft): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Sunday-first day chips: "Sun 25". */
export function dayChip(isoDate: string): string {
  const { day, date } = dayChipParts(isoDate);
  return `${day} ${date}`;
}

/** The two lines of a day chip: "Sun" above "25". */
export function dayChipParts(isoDate: string): { day: string; date: string } {
  const d = new Date(`${isoDate}T12:00:00Z`);
  return {
    day: d.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" }),
    date: String(d.getUTCDate()),
  };
}

/** Errors left once a field is edited: the field's own, and those of anything inside it
 * ("shows.2" also clears "shows.2.premiere"), go; so does "shows" when the list changes. */
export function withoutField(issues: CheckIssue[], field: string): CheckIssue[] {
  return issues.filter((i) => !(i.field === field || i.field?.startsWith(`${field}.`)));
}

/** "Sep 2004" from "2004-09-07". */
export function monthYear(isoDate: string): string {
  const d = new Date(`${isoDate.slice(0, 10)}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

/** "Sep 7, 2004" from "2004-09-07". */
export function longDay(isoDate: string): string {
  const d = new Date(`${isoDate.slice(0, 10)}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

export const PREMIERE_MIN = "1990-01-01";
export const PREMIERE_MAX = "2010-12-31";

/** A show whose real debut comes after the last year the channel carries it (or after 2010):
 * without a pretend premiere it never airs there. */
export function wontAir(debut: string | null | undefined, untilYear: number | null | undefined): boolean {
  if (!debut) return false;
  return Number(debut.slice(0, 4)) > (untilYear ?? LAST_YEAR);
}

/** "Pretend premiere Sep 2004 · really Jun 2012" (the ◇ mark is drawn beside it). */
export function premiereMeta(premiere: string, debut: string | null | undefined): string {
  const really = debut ? ` · really ${monthYear(debut)}` : "";
  return `Pretend premiere ${monthYear(premiere)}${really}`;
}

/** "Debuts Jun 2012 · won't air. Set a pretend premiere." */
export function wontAirMeta(debut: string): string {
  return `Debuts ${monthYear(debut)} · won't air. Set a pretend premiere.`;
}

/** Days between two ISO dates. */
const daysBetween = (a: string, b: string) => Math.round((Date.parse(`${b}T12:00:00Z`) - Date.parse(`${a}T12:00:00Z`)) / 86_400_000);

const addDays = (iso: string, days: number) =>
  new Date(Date.parse(`${iso}T12:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10);

/** Where a show's run lands on this channel with `premiere` set: S1E1 on the premiere, and the
 * last episode as far after it as the real run (the scheduler keeps the spacing). `span` is
 * the channel's current first and last dates for the show, if known. */
export function premiereRun(
  premiere: string,
  span: { first_air: string | null; last_air: string | null } | undefined,
): { first: string; last: string | null } {
  if (!span?.first_air || !span.last_air) return { first: premiere, last: null };
  return { first: premiere, last: addDays(premiere, daysBetween(span.first_air, span.last_air)) };
}

/** "S1E1 airs Sep 7, 2004 · last episode Mar 2006". */
export function runLine(run: { first: string; last: string | null }): string {
  const last = run.last ? ` · last episode ${monthYear(run.last)}` : "";
  return `S1E1 airs ${longDay(run.first)}${last}`;
}
