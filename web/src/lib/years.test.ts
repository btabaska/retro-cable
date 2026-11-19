import { describe, expect, it } from "vitest";
import { ALL_YEARS, parseYear, yearLabel } from "./years";

describe("years", () => {
  it("labels All Years and plain years", () => {
    expect(yearLabel(ALL_YEARS)).toBe("All Years");
    expect(yearLabel(1998)).toBe("1998");
  });

  it("reads a stored year, All Years included, and nothing else", () => {
    expect(parseYear("1998")).toBe(1998);
    expect(parseYear("0")).toBe(ALL_YEARS);
    expect(parseYear(null)).toBeNull();
    expect(parseYear("")).toBeNull();
    expect(parseYear("1989")).toBeNull();
    expect(parseYear("2011")).toBeNull();
    expect(parseYear("junk")).toBeNull();
  });
});
