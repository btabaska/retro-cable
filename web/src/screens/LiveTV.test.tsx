import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChannelInfo, Tune, TunedSlot, TvApi } from "../api/client";
import { App } from "../App";
import { BANNER_MS, LiveTV } from "./LiveTV";

const channel: ChannelInfo = {
  id: "ktvu",
  name: "KTVU",
  type: "network",
  number: 2,
  call_sign: "KTVU",
  network: null,
  on_air: true,
  library: false,
  launch: null,
};

const slot: TunedSlot = {
  kind: "episode",
  start: "2001-10-31T20:00:00-05:00",
  end: "2001-10-31T20:30:00-05:00",
  seconds: 1800,
  title: "The Simpsons",
  show: "the-simpsons",
  episode_title: "Pilot",
  season: 1,
  episode: 1,
  air_date: "1989-12-17",
  new: false,
  transplant: false,
  holiday: null,
  holiday_name: null,
  media_url: null,
};

// Simulated time is 2001; the wall clock is today. The banner must follow the wall clock.
const SIM_NOW_ISO = "2001-10-31T20:14:00-05:00";
const SIM_NOW = Date.parse(SIM_NOW_ISO);
const WALL_NOW = Date.parse("2026-11-02T12:00:00-05:00");

const noop = () => undefined;
const props = (wallNow: number, bannerUntil: number) => ({
  channel,
  dial: [channel],
  slot,
  next: null,
  now: SIM_NOW + (wallNow - WALL_NOW),
  wallNow,
  year: 2001,
  timeZone: "America/New_York",
  bannerUntil,
  onShowBanner: noop,
  onStep: noop,
  onLast: noop,
  onNumber: noop,
  onGuide: noop,
  onYear: noop,
});

describe("LiveTV banner", () => {
  it("shows the banner and clock until BANNER_MS of real time pass, with a simulated year", () => {
    const until = WALL_NOW + BANNER_MS;
    const { rerender } = render(<LiveTV {...props(WALL_NOW, until)} />);
    expect(screen.getByTestId("info-banner")).toBeInTheDocument();
    expect(screen.getByTestId("clock")).toBeInTheDocument();

    rerender(<LiveTV {...props(WALL_NOW + BANNER_MS - 1, until)} />);
    expect(screen.getByTestId("info-banner")).toBeInTheDocument();

    rerender(<LiveTV {...props(WALL_NOW + BANNER_MS, until)} />);
    expect(screen.queryByTestId("info-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("clock")).not.toBeInTheDocument();
  });
});

describe("LiveTV banner in the app", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(WALL_NOW);
    window.localStorage.clear();
    window.localStorage.setItem("rc.year", "2001");
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("hides after BANNER_MS even though the simulated clock reads 2001", async () => {
    const tune: Tune = { channel, now: SIM_NOW_ISO, offset_seconds: 840, current: slot, next: [] };
    const api: TvApi = {
      getServerVersion: () => Promise.resolve({ version: "1", commit: "x" }),
      getLineup: (year: number) =>
        Promise.resolve({
          year,
          date: "2001-10-31",
          weekday: "Wednesday",
          now: SIM_NOW_ISO,
          timezone: "America/New_York",
          channels: [channel],
        }),
      getGuide: () => Promise.reject(new Error("unused")),
      tune: () => Promise.resolve(tune),
    };
    render(<App api={api} />);
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(screen.getByTestId("info-banner")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BANNER_MS + 500);
    });
    expect(screen.queryByTestId("info-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("clock")).not.toBeInTheDocument();
  });
});
