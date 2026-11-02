import { describe, expect, it } from "vitest";
import { stepChannel } from "./channels";

describe("stepChannel", () => {
  const dial = ["ktvu", "nbc", "kpix", "kqed"];

  it("moves up and down the dial", () => {
    expect(stepChannel(dial, "nbc", 1)).toBe("kpix");
    expect(stepChannel(dial, "nbc", -1)).toBe("ktvu");
  });

  it("wraps at the ends like a cable box", () => {
    expect(stepChannel(dial, "kqed", 1)).toBe("ktvu");
    expect(stepChannel(dial, "ktvu", -1)).toBe("kqed");
  });

  it("starts at the bottom of the dial when the channel is gone", () => {
    expect(stepChannel(dial, "adult-swim", 1)).toBe("ktvu");
  });
});
