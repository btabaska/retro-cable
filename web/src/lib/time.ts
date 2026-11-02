const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(timeZone: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${timeZone}|${JSON.stringify(options)}`;
  let f = formatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { timeZone, ...options });
    formatters.set(key, f);
  }
  return f;
}

/** "8:14 PM" */
export function formatClock(t: number, timeZone: string): string {
  return formatter(timeZone, { hour: "numeric", minute: "2-digit" }).format(t);
}

/** "8:14", the way guide cells label start times. */
export function formatShortTime(t: number, timeZone: string): string {
  return formatClock(t, timeZone).replace(/\s?[AP]M$/, "");
}

/** "Saturday, Oct 31, 1998" */
export function formatLongDate(t: number, timeZone: string): string {
  return formatter(timeZone, {
    weekday: "long",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(t);
}

/** "Sat 10/31" */
export function formatGuideDay(t: number, timeZone: string): string {
  return formatter(timeZone, { weekday: "short", month: "numeric", day: "numeric" })
    .format(t)
    .replace(",", "");
}

/** "Thu, Sep 24, 1998" for an ISO date (yyyy-mm-dd). */
export function formatAirDate(isoDate: string): string {
  return formatter("UTC", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(Date.parse(`${isoDate}T12:00:00Z`));
}

/** The server's simulated time (`serverNow`, fetched at `fetchedAt`) advanced to `now`. */
export function simulatedNow(serverNow: string, fetchedAt: number, now: number): number {
  return Date.parse(serverNow) + (now - fetchedAt);
}

/** Local calendar parts of an instant in a zone, for building /guide queries. */
export function zonedParts(t: number, timeZone: string): { date: string; time: string } {
  const parts = formatter(timeZone, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(t);
  const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value ?? "00";
  return { date: `${get("month")}-${get("day")}`, time: `${get("hour")}:${get("minute")}` };
}
