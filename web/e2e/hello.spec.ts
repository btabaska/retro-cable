import { expect, test } from "@playwright/test";

test("shows the server /version on the power-on screen", async ({ page, request }) => {
  const response = await request.get("/api/version");
  expect(response.ok()).toBe(true);
  const { version, commit } = (await response.json()) as { version: string; commit: string };

  await page.goto("/");
  await expect(page.getByTestId("server-version")).toHaveText(`Server v${version} · ${commit}`);
});

test("the 1920x1080 screen scales to the window", async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 540 });
  await page.goto("/");
  const box = await page.getByTestId("stage").boundingBox();
  expect(box).not.toBeNull();
  expect(box?.width).toBeCloseTo(960, 0);
  expect(box?.height).toBeCloseTo(540, 0);
});
