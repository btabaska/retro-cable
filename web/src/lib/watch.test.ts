import { describe, expect, it, vi } from "vitest";
import { MIN_SECONDS, postWatch, type Watching, type WatchReport, WatchReporter } from "./watch";

const START = "1995-10-31T20:00:00-05:00";

function watching(over: Partial<Watching> = {}, slot: Partial<Watching["slot"]> = {}): Watching {
  return {
    channel: "ktvu",
    year: 1995,
    simNow: Date.parse(START) + 90_000, // tuned in 90 s into the program
    ...over,
    slot: { start: START, seconds: 1320, kind: "episode", show: "the-simpsons", season: 4, episode: 12, ...slot },
  };
}

function setup() {
  let now = 1_000_000;
  const sent: { report: WatchReport; beacon: boolean }[] = [];
  let id = 0;
  const reporter = new WatchReporter(
    (report, beacon) => sent.push({ report, beacon }),
    () => now,
    () => `session-${++id}`,
  );
  const tick = (seconds: number) => {
    now += seconds * 1000;
  };
  return { reporter, sent, tick };
}

describe("WatchReporter", () => {
  it("reports a program with heartbeats, then once more when the next one starts", () => {
    const { reporter, sent, tick } = setup();
    reporter.watch(watching());
    tick(60);
    reporter.heartbeat();
    expect(sent).toHaveLength(1);
    expect(sent[0]?.report).toMatchObject({
      session: "session-1",
      channel: "ktvu",
      item: "the-simpsons",
      kind: "episode",
      season: 4,
      episode: 12,
      year: 1995,
      offset: 90,
      seconds: 60,
      runtime: 1320,
      ended: null,
    });
    tick(600);
    // The same program again (a re-render): nothing new.
    reporter.watch(watching());
    expect(sent).toHaveLength(1);
    reporter.watch(watching({}, { start: "1995-10-31T20:22:00-05:00", show: "x-men" }));
    expect(sent[1]?.report).toMatchObject({ session: "session-1", seconds: 660, ended: "program_end" });
  });

  it("calls changing channels or years a tune-away, and never reports surfing", () => {
    const { reporter, sent, tick } = setup();
    reporter.watch(watching());
    tick(MIN_SECONDS - 1);
    reporter.watch(watching({ channel: "kpix" }));
    expect(sent).toHaveLength(0); // flipped past in under 10 s
    tick(200);
    reporter.watch(watching({ channel: "kpix", year: 2001 }));
    expect(sent[0]?.report).toMatchObject({ channel: "kpix", ended: "tune_away", seconds: 200 });
    tick(30);
    reporter.watch(null); // tuning, or the player hidden
    expect(sent[1]?.report).toMatchObject({ year: 2001, ended: "tune_away" });
  });

  it("ends with program_end on a bumper of the same channel and ignores filler", () => {
    const { reporter, sent, tick } = setup();
    reporter.watch(watching());
    tick(1230);
    reporter.watch(watching({}, { kind: "bumper", show: null, start: "1995-10-31T20:22:00-05:00" }));
    expect(sent[0]?.report.ended).toBe("program_end");
    tick(600);
    reporter.watch(watching({}, { kind: "filler", show: null, start: "1995-10-31T20:23:00-05:00" }));
    reporter.heartbeat();
    expect(sent).toHaveLength(1);
  });

  it("caps seconds at the runtime and reports power-off by beacon only once", () => {
    const { reporter, sent, tick } = setup();
    reporter.watch(watching());
    tick(5000);
    reporter.flush();
    expect(sent[0]).toMatchObject({ beacon: true, report: { seconds: 1320, ended: null } });
    reporter.end("power_off", true);
    reporter.end("power_off", true);
    reporter.heartbeat();
    expect(sent).toHaveLength(2);
    expect(sent[1]).toMatchObject({ beacon: true, report: { ended: "power_off" } });
  });

  it("never throws when sending fails", () => {
    let now = 0;
    const reporter = new WatchReporter(
      () => {
        throw new Error("offline");
      },
      () => now,
    );
    reporter.watch(watching());
    now += 60_000;
    expect(() => {
      reporter.heartbeat();
      reporter.end("tune_away");
    }).not.toThrow();
  });
});

describe("postWatch", () => {
  const report: WatchReport = {
    session: "abcdefgh12",
    channel: "ktvu",
    item: "the-simpsons",
    kind: "episode",
    year: 1995,
    offset: 0,
    seconds: 60,
    runtime: 1320,
  };

  it("uses sendBeacon when the page is going away, else fetch keepalive", () => {
    const beacon = vi.fn(() => true);
    const fetcher = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })));
    vi.stubGlobal("navigator", { sendBeacon: beacon });
    vi.stubGlobal("fetch", fetcher);
    postWatch(report, true);
    expect(beacon).toHaveBeenCalledOnce();
    expect(fetcher).not.toHaveBeenCalled();
    postWatch(report, false);
    expect(fetcher).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/watch$/),
      expect.objectContaining({ method: "POST", keepalive: true }),
    );
    vi.unstubAllGlobals();
  });

  it("stays silent when the network fails", async () => {
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("down"))));
    expect(() => {
      postWatch(report, true);
    }).not.toThrow();
    await Promise.resolve();
    vi.stubGlobal("fetch", () => {
      throw new TypeError("bad url");
    });
    expect(() => {
      postWatch(report, false);
    }).not.toThrow();
    vi.unstubAllGlobals();
  });
});
