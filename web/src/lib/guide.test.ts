import { describe, expect, it } from "vitest";
import type { GuideSlot } from "../api/client";
import { cellIndexAt, layoutRow, windowStartFor } from "./guide";

const slot = (kind: GuideSlot["kind"], start: string, end: string, title: string = kind): GuideSlot => ({
  kind,
  start,
  end,
  seconds: (Date.parse(end) - Date.parse(start)) / 1000,
  title,
  show: null,
  episode_title: "",
  season: null,
  episode: null,
  air_date: null,
  new: false,
  transplant: false,
  holiday: null,
  holiday_name: null,
});

const T = (hhmmss: string) => `1998-10-31T${hhmmss}-05:00`;
const ms = (hhmmss: string) => Date.parse(T(hhmmss));

describe("layoutRow", () => {
  const slots = [
    slot("episode", T("19:52:00"), T("20:14:00"), "Nova"),
    slot("bumper", T("20:14:00"), T("20:14:10")),
    slot("episode", T("20:14:10"), T("20:36:10"), "Arthur"),
    slot("bumper", T("20:36:10"), T("20:36:20")),
    slot("movie", T("20:36:20"), T("22:06:20"), "Halloweentown"),
  ];

  it("hides bumpers inside the next program's cell and sizes cells by run time", () => {
    const cells = layoutRow(slots, ms("20:00:00"), ms("21:30:00"));
    expect(cells.map((c) => c.slot.title)).toEqual(["Nova", "Arthur", "Halloweentown"]);
    const [nova, arthur, movie] = cells;
    expect(nova?.left).toBe(0);
    expect(nova?.clippedStart).toBe(true);
    // Nova's cell runs to Arthur's start, absorbing the bumper.
    expect(nova?.end).toBe(ms("20:14:10"));
    expect(arthur?.left).toBeCloseTo((14 * 60 + 10) / 5400);
    expect((movie?.left ?? 0) + (movie?.width ?? 0)).toBeCloseTo(1);
  });

  it("drops cells outside the window", () => {
    expect(layoutRow(slots, ms("21:00:00"), ms("22:30:00")).map((c) => c.slot.title)).toEqual([
      "Halloweentown",
    ]);
  });

  it("finds the cell covering a time, or the nearest", () => {
    const cells = layoutRow(slots, ms("20:00:00"), ms("21:30:00"));
    expect(cellIndexAt(cells, ms("20:20:00"))).toBe(1);
    expect(cellIndexAt(cells, ms("23:00:00"))).toBe(2);
    expect(cellIndexAt(cells, ms("18:00:00"))).toBe(0);
  });
});

describe("windowStartFor", () => {
  it("rounds down to the half hour", () => {
    expect(windowStartFor(ms("20:14:33"))).toBe(ms("20:00:00"));
    expect(windowStartFor(ms("20:44:00"))).toBe(ms("20:30:00"));
  });
});
