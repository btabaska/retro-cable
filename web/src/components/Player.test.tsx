import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TunedSlot } from "../api/client";
import { Player } from "./Player";

const slot = (over: Partial<TunedSlot>): TunedSlot => ({
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
  it("shows a bumper slate with what's up next when the bumper has no media", () => {
    render(
      <Player
        slot={slot({ kind: "bumper" })}
        next={slot({ title: "Seinfeld", start: "1998-10-31T20:27:00-05:00", show: "seinfeld" })}
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
