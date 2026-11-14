export const FIRST_YEAR = 1990;
export const LAST_YEAR = 2010;
/** The year the server takes for All Years: every channel, every episode, today's real date. */
export const ALL_YEARS = 0;

/** "1998", or "All Years". */
export function yearLabel(year: number): string {
  return year === ALL_YEARS ? "All Years" : String(year);
}

/** A year saved in storage, or null when there is none or it isn't one the server takes. */
export function parseYear(raw: string | null): number | null {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const year = Number(raw);
  return year === ALL_YEARS || (year >= FIRST_YEAR && year <= LAST_YEAR) ? year : null;
}
