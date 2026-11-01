import { describe, expect, it } from "vitest";
import { moveFocus } from "./focus";

const grid = { rows: 1, cols: 3 };

describe("moveFocus", () => {
  it("moves right and left within a row", () => {
    expect(moveFocus({ row: 0, col: 0 }, "ArrowRight", grid)).toEqual({ row: 0, col: 1 });
    expect(moveFocus({ row: 0, col: 2 }, "ArrowLeft", grid)).toEqual({ row: 0, col: 1 });
  });

  it("stops at the edges like a cable box", () => {
    expect(moveFocus({ row: 0, col: 2 }, "ArrowRight", grid)).toEqual({ row: 0, col: 2 });
    expect(moveFocus({ row: 0, col: 0 }, "ArrowLeft", grid)).toEqual({ row: 0, col: 0 });
    expect(moveFocus({ row: 0, col: 1 }, "ArrowUp", grid)).toEqual({ row: 0, col: 1 });
  });

  it("moves between rows", () => {
    const tall = { rows: 3, cols: 3 };
    expect(moveFocus({ row: 0, col: 1 }, "ArrowDown", tall)).toEqual({ row: 1, col: 1 });
    expect(moveFocus({ row: 1, col: 1 }, "ArrowUp", tall)).toEqual({ row: 0, col: 1 });
  });

  it("returns null for keys it does not handle", () => {
    expect(moveFocus({ row: 0, col: 0 }, "Enter", grid)).toBeNull();
  });
});
