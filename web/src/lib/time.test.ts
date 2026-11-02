import { describe, expect, it } from "vitest";
import { formatClock, formatLongDate, formatShortTime, simulatedNow } from "./time";

const tz = "America/New_York";
const t = Date.parse("1998-10-31T20:14:00-05:00");

describe("time formatting in the configured zone", () => {
  it("formats the clock", () => {
    expect(formatClock(t, tz)).toBe("8:14 PM");
    expect(formatShortTime(t, tz)).toBe("8:14");
  });

  it("formats the long date", () => {
    expect(formatLongDate(t, tz)).toBe("Saturday, Oct 31, 1998");
  });
});

describe("simulatedNow", () => {
  it("advances the server's simulated time by real elapsed time", () => {
    expect(simulatedNow("1998-10-31T20:14:00-05:00", 1_000, 61_000)).toBe(t + 60_000);
  });
});

describe("zonedParts", () => {
  it("gives MM-DD and HH:MM in the zone", async () => {
    const { zonedParts } = await import("./time");
    expect(zonedParts(t, tz)).toEqual({ date: "10-31", time: "20:14" });
    expect(zonedParts(Date.parse("1998-11-01T04:30:00Z"), tz)).toEqual({ date: "10-31", time: "23:30" });
  });
});
