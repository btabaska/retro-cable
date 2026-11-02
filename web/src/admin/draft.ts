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
  const d = new Date(`${isoDate}T12:00:00Z`);
  const weekday = d.toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
  return `${weekday} ${d.getUTCDate()}`;
}
