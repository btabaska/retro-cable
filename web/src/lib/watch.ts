import type { components } from "../api/generated/schema";

/**
 * Viewing reports for the weekly station manager (M5): one session per program watched on a
 * channel. Fire and forget: sending never blocks or slows tuning, and failures are silent.
 * No URLs, tokens or file paths are sent; the server stamps the time.
 */
export type WatchReport = components["schemas"]["WatchReport"];
export type EndReason = NonNullable<WatchReport["ended"]>;
/** Delivers one report; `beacon` asks for sendBeacon (the page is going away). Never throws. */
export type SendWatch = (report: WatchReport, beacon: boolean) => void;

/** Less than this is flipping past a channel, not watching it: never reported. */
export const MIN_SECONDS = 10;
/** How often an open session is reported while it plays. */
export const HEARTBEAT_MS = 60_000;

/** What is on screen: the slot playing on a channel in a year, and the simulated now. */
export interface Watching {
  channel: string;
  year: number;
  slot: {
    start: string;
    seconds: number;
    kind: string;
    show: string | null;
    season: number | null;
    episode: number | null;
  };
  /** Simulated now (ms), to work out how far into the program the viewer tuned in. */
  simNow: number;
}

interface Open {
  key: string;
  channel: string;
  year: number;
  /** Real Date.now() when it began playing. */
  since: number;
  report: Omit<WatchReport, "seconds" | "ended">;
  ended: boolean;
}

const keyOf = (w: Watching) => `${w.channel}|${w.year}|${w.slot.start}`;

function randomId(): string {
  try {
    return crypto.randomUUID().replace(/-/g, "");
  } catch {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  }
}

/** Keeps the open session and decides when to report it. Pure apart from `send` and `now`. */
export class WatchReporter {
  private open: Open | null = null;

  constructor(
    private readonly send: SendWatch,
    private readonly now: () => number = () => Date.now(),
    private readonly makeId: () => string = randomId,
  ) {}

  /** Call whenever what is playing may have changed; null when nothing is being watched. */
  watch(next: Watching | null): void {
    const program = next && (next.slot.kind === "episode" || next.slot.kind === "movie") && next.slot.show ? next : null;
    if (this.open && program && this.open.key === keyOf(program)) return;
    if (this.open) {
      const sameChannel = next !== null && next.channel === this.open.channel && next.year === this.open.year;
      this.end(sameChannel ? "program_end" : "tune_away");
    }
    if (!program) return;
    const offset = Math.max(0, Math.round((program.simNow - Date.parse(program.slot.start)) / 1000));
    this.open = {
      key: keyOf(program),
      channel: program.channel,
      year: program.year,
      since: this.now(),
      ended: false,
      report: {
        session: this.makeId(),
        channel: program.channel,
        item: program.slot.show ?? "",
        kind: program.slot.kind === "movie" ? "movie" : "episode",
        season: program.slot.season,
        episode: program.slot.episode,
        year: program.year,
        offset: Math.min(offset, program.slot.seconds),
        runtime: Math.max(1, program.slot.seconds),
      },
    };
  }

  /** The periodic report while a program plays. */
  heartbeat(): void {
    this.report(null, false);
  }

  /** The page was hidden: report what we have now, in case it never comes back. */
  flush(): void {
    this.report(null, true);
  }

  /** The program stopped being watched. */
  end(reason: EndReason, beacon = false): void {
    this.report(reason, beacon);
    this.open = null;
  }

  private report(ended: EndReason | null, beacon: boolean): void {
    const open = this.open;
    if (!open || open.ended) return;
    const seconds = Math.min(open.report.runtime, Math.floor((this.now() - open.since) / 1000));
    if (seconds < MIN_SECONDS) return;
    if (ended) open.ended = true;
    try {
      this.send({ ...open.report, seconds, ended }, beacon);
    } catch {
      // Reporting must never get in the way of watching.
    }
  }
}

/** POST /api/watch, with sendBeacon when the page is going away, else fetch keepalive. */
export function postWatch(report: WatchReport, beacon: boolean): void {
  const url = `${window.location.origin}/api/watch`;
  const body = JSON.stringify(report);
  try {
    if (beacon && typeof navigator.sendBeacon === "function") {
      if (navigator.sendBeacon(url, new Blob([body], { type: "application/json" }))) return;
    }
    fetch(url, { method: "POST", body, keepalive: true, headers: { "Content-Type": "application/json" } }).catch(
      () => undefined,
    );
  } catch {
    // Silent: a lost report costs the station manager one data point, nothing more.
  }
}
