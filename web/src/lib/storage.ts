/** localStorage that never throws (private windows, blocked storage). */
export function load(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function save(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Remembering the year and channel is a convenience; ignore failures.
  }
}
