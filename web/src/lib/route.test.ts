import { describe, expect, it } from "vitest";
import { isAdminPath } from "./route";

describe("isAdminPath", () => {
  it("routes /admin to the admin", () => {
    expect(isAdminPath("/admin")).toBe(true);
    expect(isAdminPath("/admin/")).toBe(true);
    expect(isAdminPath("/")).toBe(false);
    expect(isAdminPath("/administrator")).toBe(false);
  });
});
