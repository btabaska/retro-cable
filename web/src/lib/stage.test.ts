import { describe, expect, it } from "vitest";
import { fitStage } from "./stage";

describe("fitStage", () => {
  it("keeps a 1920x1080 window at scale 1", () => {
    expect(fitStage(1920, 1080)).toEqual({ scale: 1, left: 0, top: 0 });
  });

  it("scales down a smaller 16:9 window", () => {
    expect(fitStage(960, 540)).toEqual({ scale: 0.5, left: 0, top: 0 });
  });

  it("letterboxes a window that is too tall", () => {
    const { scale, left, top } = fitStage(960, 1000);
    expect(scale).toBe(0.5);
    expect(left).toBe(0);
    expect(top).toBe((1000 - 540) / 2);
  });

  it("pillarboxes a window that is too wide", () => {
    const { scale, left, top } = fitStage(3000, 1080);
    expect(scale).toBe(1);
    expect(left).toBe((3000 - 1920) / 2);
    expect(top).toBe(0);
  });
});
