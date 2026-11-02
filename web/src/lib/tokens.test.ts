import { describe, expect, it } from "vitest";
import { tokensToCssVars } from "./tokens";

describe("tokensToCssVars", () => {
  it("names variables --rc-<group>-<kebab-name>", () => {
    expect(tokensToCssVars({ color: { textMuted: "#B9C8EA" } })).toEqual({
      "--rc-color-text-muted": "#B9C8EA",
    });
  });

  it("gives numbers a px unit", () => {
    expect(tokensToCssVars({ screen: { width: 1920 } })).toEqual({ "--rc-screen-width": "1920px" });
  });

  it("skips $-prefixed metadata inside a group", () => {
    expect(tokensToCssVars({ admin: { $description: "phone", inkMuted: "#4A5878" } })).toEqual({
      "--rc-admin-ink-muted": "#4A5878",
    });
  });

  it("skips $-prefixed metadata keys", () => {
    expect(tokensToCssVars({ $description: "notes", space: { gutter: "64px" } })).toEqual({
      "--rc-space-gutter": "64px",
    });
  });
});
