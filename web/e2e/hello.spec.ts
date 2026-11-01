import { expect, test } from "@playwright/test";

test("shows the server /version", async ({ page, request }) => {
  const response = await request.get("/api/version");
  expect(response.ok()).toBe(true);
  const { version, commit } = (await response.json()) as { version: string; commit: string };

  await page.goto("/");
  await expect(page.getByTestId("server-version")).toHaveText(`Server v${version} · ${commit}`);
});

test("arrow keys move focus between guide cells", async ({ page }) => {
  await page.goto("/");
  const cells = page.getByRole("gridcell");
  await expect(cells).toHaveCount(3);
  await expect(cells.nth(0)).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(cells.nth(1)).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect(cells.nth(2)).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(cells.nth(1)).toBeFocused();
});

test("the 1920x1080 screen scales to the window", async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 540 });
  await page.goto("/");
  const box = await page.getByTestId("stage").boundingBox();
  expect(box).not.toBeNull();
  expect(box?.width).toBeCloseTo(960, 0);
  expect(box?.height).toBeCloseTo(540, 0);
});
