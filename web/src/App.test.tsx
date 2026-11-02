import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChannelInfo, Guide, Lineup, Tune, TunedSlot, TvApi } from "./api/client";
import { App } from "./App";

const channel = (id: string, number: number, call_sign: string, on_air = true): ChannelInfo => ({
  id,
  name: call_sign,
  type: "network",
  number,
  call_sign,
  network: null,
  on_air,
  library: false,
  launch: on_air ? null : "2001-09-02",
});

const CHANNELS = [channel("ktvu", 2, "KTVU"), channel("nbc", 4, "KRON"), channel("adult-swim", 46, "ADSM", false)];
const NOW = "1998-10-31T20:14:00-05:00";

const lineup = (year: number): Lineup => ({
  year,
  date: `${year}-10-31`,
  weekday: "Saturday",
  now: NOW.replace("1998", String(year)),
  timezone: "America/New_York",
  channels: CHANNELS,
});

const slot = (title: string, start: string, end: string): TunedSlot => ({
  kind: "episode",
  start,
  end,
  seconds: (Date.parse(end) - Date.parse(start)) / 1000,
  title,
  show: title.toLowerCase(),
  episode_title: "Pilot",
  season: 1,
  episode: 1,
  air_date: "1998-09-24",
  new: false,
  transplant: false,
  holiday: null,
  holiday_name: null,
  media_url: null,
});

const tuneFor = (id: string): Tune => {
  const title = id === "ktvu" ? "The Simpsons" : "Friends";
  return {
    channel: CHANNELS.find((c) => c.id === id) ?? channel(id, 99, "X"),
    now: NOW,
    offset_seconds: 600,
    current: slot(title, "1998-10-31T20:04:00-05:00", "1998-10-31T20:27:00-05:00"),
    next: [slot("Seinfeld", "1998-10-31T20:27:00-05:00", "1998-10-31T20:50:00-05:00")],
  };
};

const guide: Guide = {
  date: "1998-10-31",
  weekday: "Saturday",
  timezone: "America/New_York",
  start: "1998-10-31T20:00:00-05:00",
  end: "1998-11-01T09:30:00-05:00",
  channels: CHANNELS.filter((c) => c.on_air).map((c) => ({
    channel: c,
    slots: [tuneFor(c.id).current, ...tuneFor(c.id).next],
  })),
};

function fakeApi(): TvApi {
  return {
    getServerVersion: vi.fn(() => Promise.resolve({ version: "1.2.3", commit: "abc1234" })),
    getLineup: vi.fn((year: number) => Promise.resolve(lineup(year))),
    getGuide: vi.fn(() => Promise.resolve(guide)),
    tune: vi.fn((id: string) => Promise.resolve(tuneFor(id))),
  };
}

describe("App", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows the server version on the power-on screen", async () => {
    render(<App api={fakeApi()} />);
    expect(await screen.findByTestId("server-version")).toHaveTextContent("Server v1.2.3 · abc1234");
  });

  it("says when the server is unreachable", async () => {
    const api = { ...fakeApi(), getServerVersion: () => Promise.reject(new Error("down")) };
    render(<App api={api} />);
    expect(await screen.findByTestId("server-version")).toHaveTextContent("No signal");
  });

  it("powers on to the first channel with the info banner", async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} />);
    await user.keyboard("{Enter}");
    expect(await screen.findByTestId("banner-title")).toHaveTextContent("The Simpsons");
    expect(screen.getByTestId("banner-number")).toHaveTextContent("02");
  });

  it("changes channel and goes back to the last one", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<App api={api} />);
    await user.keyboard("{Enter}");
    await screen.findByTestId("banner-title");
    await user.keyboard("{ArrowUp}");
    await vi.waitFor(() => {
      expect(screen.getByTestId("banner-title")).toHaveTextContent("Friends");
    });
    expect(screen.getByTestId("banner-number")).toHaveTextContent("04");
    await user.keyboard(" ");
    await vi.waitFor(() => {
      expect(screen.getByTestId("banner-title")).toHaveTextContent("The Simpsons");
    });
    // Adult Swim isn't on the dial in 1998, so channel up wraps past it.
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(await screen.findByTestId("banner-number")).toHaveTextContent("02");
  });

  it("opens the guide and tunes from it", async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} />);
    await user.keyboard("{Enter}");
    await screen.findByTestId("banner-title");
    await user.keyboard("{Enter}");
    expect(await screen.findByTestId("guide")).toBeInTheDocument();
    expect(await screen.findByTestId("details-title")).toHaveTextContent("The Simpsons");
    await user.keyboard("{ArrowDown}");
    expect(screen.getByTestId("details-title")).toHaveTextContent("Friends");
    await user.keyboard("{Enter}");
    expect(await screen.findByTestId("banner-number")).toHaveTextContent("04");
  });

  it("changes the year from the guide", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<App api={api} />);
    await user.keyboard("{Enter}");
    await screen.findByTestId("banner-title");
    await user.keyboard("{Enter}");
    await screen.findByTestId("guide");
    await user.keyboard(" ");
    expect(await screen.findByTestId("year-picker")).toBeInTheDocument();
    await user.keyboard("{ArrowRight}");
    await act(() => new Promise((r) => setTimeout(r, 200)));
    expect(await screen.findByTestId("year-date")).toHaveTextContent("Saturday, October 31, 1999");
    await user.keyboard("{Enter}");
    await screen.findByTestId("banner-title");
    expect(api.tune).toHaveBeenLastCalledWith("ktvu", 1999);
    expect(window.localStorage.getItem("rc.year")).toBe("1999");
  });
});
