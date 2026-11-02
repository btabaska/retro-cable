import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TunedSlot } from "../api/client";
import { DURATION_MISMATCH_S, OVERRUN_TOLERANCE_MS, Player } from "./Player";

const WALL_NOW = Date.parse("2026-11-02T12:00:00-05:00");
const SLOT_START = Date.parse("1998-10-31T20:00:00-05:00");
const SLOT_SECONDS = 1320;

const slot: TunedSlot = {
  kind: "episode",
  start: new Date(SLOT_START).toISOString(),
  end: new Date(SLOT_START + SLOT_SECONDS * 1000).toISOString(),
  seconds: SLOT_SECONDS,
  title: "Johnny Bravo",
  show: "johnny-bravo",
  episode_title: "Pilot",
  season: 1,
  episode: 1,
  air_date: "1997-07-14",
  new: false,
  transplant: false,
  holiday: null,
  holiday_name: null,
  media_url: "/media/fixture/ep.mp4?sig=x",
};

/** Simulated now starts 10 s before the slot ends and moves with the (fake) wall clock. */
const simNow = () => SLOT_START + (SLOT_SECONDS - 10) * 1000 + (Date.now() - WALL_NOW);

interface FakeMedia {
  duration: number;
  currentTime: number;
  paused: boolean;
  ended: boolean;
}

function fakeMedia(el: HTMLVideoElement, media: FakeMedia) {
  for (const key of Object.keys(media) as (keyof FakeMedia)[]) {
    Object.defineProperty(el, key, {
      configurable: true,
      get: () => media[key],
      set: (v: never) => {
        media[key] = v;
      },
    });
  }
  el.play = () => Promise.resolve();
}

const bumperSlot = (over: Partial<TunedSlot>): TunedSlot => ({
  kind: "episode",
  start: "1998-10-31T20:26:50-05:00",
  end: "1998-10-31T20:27:00-05:00",
  seconds: 10,
  title: "KTVU (Fox)",
  show: null,
  episode_title: "",
  season: null,
  episode: null,
  air_date: null,
  new: false,
  transplant: false,
  holiday: null,
  holiday_name: null,
  media_url: null,
  ...over,
});

describe("Player", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(WALL_NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  const renderPlayer = (onEnded: () => void) =>
    render(
      <Player slot={slot} next={null} callSign="02 KTVU" mode="live" muted simNow={simNow} onEnded={onEnded} />,
    );

  it("lets a program that drifted a little past its slot end finish before advancing", async () => {
    const onEnded = vi.fn();
    renderPlayer(onEnded);
    const media = { duration: SLOT_SECONDS, currentTime: SLOT_SECONDS - 2, paused: false, ended: false };
    fakeMedia(screen.getByTestId<HTMLVideoElement>("player-video"), media);

    // When the schedule says the slot is over, the file (2 s behind) still has 2 s to play.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_100);
    });
    expect(onEnded).not.toHaveBeenCalled();

    media.ended = true;
    fireEvent(screen.getByTestId("player-video"), new Event("ended"));
    expect(onEnded).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(OVERRUN_TOLERANCE_MS * 2);
    });
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  it("advances once the drift tolerance runs out even if the ended event never fires", async () => {
    const onEnded = vi.fn();
    renderPlayer(onEnded);
    const media = { duration: SLOT_SECONDS, currentTime: SLOT_SECONDS - 2, paused: false, ended: false };
    fakeMedia(screen.getByTestId<HTMLVideoElement>("player-video"), media);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_100 + OVERRUN_TOLERANCE_MS + 500);
    });
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  it("advances on the schedule when the file is already done", async () => {
    const onEnded = vi.fn();
    renderPlayer(onEnded);
    fakeMedia(screen.getByTestId<HTMLVideoElement>("player-video"), {
      duration: SLOT_SECONDS,
      currentTime: SLOT_SECONDS,
      paused: true,
      ended: true,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_100);
    });
    expect(onEnded).toHaveBeenCalledTimes(1);
  });

  it("reports a file whose real length differs from the slot instead of hiding it", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    renderPlayer(vi.fn());
    const el = screen.getByTestId<HTMLVideoElement>("player-video");
    fakeMedia(el, { duration: 120, currentTime: 0, paused: false, ended: false });
    fireEvent(el, new Event("loadedmetadata"));
    expect(warn).toHaveBeenCalledTimes(1);
    const message = String(warn.mock.calls[0]?.[0]);
    expect(message).toContain("Johnny Bravo");
    expect(message).toContain("120");
    expect(message).toContain(String(SLOT_SECONDS));
    // Never log the signed media URL.
    expect(message).not.toContain("sig=");
    expect(message).not.toContain("/media/");
    expect(screen.getByTestId("player")).toHaveAttribute("data-duration-mismatch", String(120 - SLOT_SECONDS));
  });

  it("stays quiet when the file matches the slot within a few seconds", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    renderPlayer(vi.fn());
    const el = screen.getByTestId<HTMLVideoElement>("player-video");
    fakeMedia(el, { duration: SLOT_SECONDS + DURATION_MISMATCH_S - 0.5, currentTime: 0, paused: false, ended: false });
    fireEvent(el, new Event("loadedmetadata"));
    expect(warn).not.toHaveBeenCalled();
    expect(screen.getByTestId("player")).not.toHaveAttribute("data-duration-mismatch");
  });

  it("shows a bumper slate with what's up next when the bumper has no media", () => {
    render(
      <Player
        slot={bumperSlot({ kind: "bumper" })}
        next={bumperSlot({ title: "Seinfeld", start: "1998-10-31T20:27:00-05:00", show: "seinfeld" })}
        callSign="02 KTVU"
        channelName="KTVU (Fox)"
        timeZone="America/New_York"
        mode="live"
        muted
        simNow={() => Date.parse("1998-10-31T20:26:55-05:00")}
        onEnded={() => undefined}
      />,
    );
    const bumper = screen.getByTestId("bumper-slate");
    expect(bumper).toHaveTextContent("02 KTVU");
    expect(bumper).toHaveTextContent("KTVU (Fox)");
    expect(bumper).toHaveTextContent("Up next 8:27 Seinfeld");
    expect(screen.queryByTestId("player-filler")).toBeNull();
  });
});
