import { describe, expect, it } from "vitest";
import type { GuideSlot } from "../api/client";
import { duration, kicker, programLabel } from "./program";

const base: GuideSlot = {
  kind: "episode",
  start: "1998-10-31T20:00:00-05:00",
  end: "1998-10-31T20:22:00-05:00",
  seconds: 1320,
  title: "The Simpsons",
  show: "the-simpsons",
  episode_title: "Treehouse of Horror IX",
  season: 10,
  episode: 4,
  air_date: "1998-10-25",
  new: false,
  transplant: false,
  holiday: null,
  holiday_name: null,
};

describe("kicker", () => {
  it("is empty for a plain rerun", () => {
    expect(kicker(base)).toEqual([]);
  });

  it("orders NEW then the holiday, holiday in capitals", () => {
    expect(kicker({ ...base, new: true, holiday: "halloween", holiday_name: "Halloween" })).toEqual([
      { text: "NEW", tone: "new" },
      { text: "HALLOWEEN", tone: "holiday" },
    ]);
  });

  it("marks movies", () => {
    const movie = { ...base, kind: "movie" as const, season: null, episode: null, episode_title: "" };
    expect(kicker(movie)).toEqual([{ text: "MOVIE", tone: "plain" }]);
    expect(kicker({ ...movie, holiday: "halloween", holiday_name: "Halloween" })).toEqual([
      { text: "MOVIE", tone: "plain" },
      { text: "HALLOWEEN", tone: "holiday" },
    ]);
  });

  it("never marks bumpers or filler", () => {
    expect(kicker({ ...base, kind: "bumper", new: true })).toEqual([]);
    expect(kicker({ ...base, kind: "filler", holiday_name: "Halloween" })).toEqual([]);
  });
});

describe("programLabel", () => {
  it("shows episode title and S/E for episodes", () => {
    expect(programLabel(base)).toBe("Treehouse of Horror IX · S10 E4");
  });

  it("shows the year and running time for movies", () => {
    const movie = {
      ...base,
      kind: "movie" as const,
      title: "Halloweentown",
      season: null,
      episode: null,
      episode_title: "",
      air_date: "1998-10-17",
      seconds: 84 * 60 + 30,
    };
    expect(programLabel(movie)).toBe("1998 · 1h 24m");
  });
});

describe("duration", () => {
  it("formats hours and minutes", () => {
    expect(duration(85 * 60)).toBe("1h 25m");
    expect(duration(22 * 60)).toBe("22m");
    expect(duration(120 * 60)).toBe("2h");
  });
});
