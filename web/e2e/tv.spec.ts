import { expect, type Page, test } from "@playwright/test";

// Runs against the demo database (make demo-db): every carried show has episodes.
const shot = (page: Page, name: string) => page.screenshot({ path: `test-results/screens/${name}.png` });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("rc.year", "1998");
    window.localStorage.removeItem("rc.channel");
  });
});

test("launch, banner, guide, tune and change year", async ({ page, request }) => {
  await page.goto("/");
  await shot(page, "1-power-on");
  await page.keyboard.press("Enter");

  // Launch: the first channel on the dial, with the info banner.
  const banner = page.getByTestId("info-banner");
  await expect(banner).toBeVisible();
  await expect(page.getByTestId("banner-number")).toHaveText("02");
  await expect(page.getByTestId("banner-title")).not.toHaveText("Tuning…");

  // The player starts the file at the offset /tune returned.
  const tuned = (await (await request.get("/api/tune/ktvu?year=1998")).json()) as {
    offset_seconds: number;
    current: { kind: string; media_url: string | null };
  };
  if (tuned.current.media_url) {
    const video = page.getByTestId("player-video");
    await expect
      .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 10_000 })
      .toBeGreaterThan(Math.max(0, tuned.offset_seconds - 5));
  }
  await shot(page, "2-live-banner");

  // Channel up.
  const shownAt = Date.now();
  await page.keyboard.press("ArrowUp");
  await expect(page.getByTestId("banner-number")).toHaveText("04");

  // The banner and clock hide after ~4 s of real time, even though the clock reads 1998.
  await expect(banner).toBeHidden({ timeout: 8_000 });
  await expect(page.getByTestId("clock")).toBeHidden();
  expect(Date.now() - shownAt).toBeGreaterThan(3_000);
  await page.keyboard.press("i");
  await expect(banner).toBeVisible();
  await expect(banner).toBeHidden({ timeout: 8_000 });

  // Guide: opens on the current channel; down one row and tune.
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("guide")).toBeVisible();
  await expect(page.getByTestId("details-title")).not.toHaveText("Loading the guide…");
  await expect(page.getByRole("gridcell", { selected: true })).toHaveCount(1);
  await shot(page, "3-guide");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("banner-number")).toHaveText("05");

  // Year picker from the guide: 1997 is a Friday schedule.
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("guide")).toBeVisible();
  await page.keyboard.press(" ");
  await expect(page.getByTestId("year-picker")).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("year-date")).toContainText("1997");
  await expect(page.getByTestId("year-dial")).toContainText("Adult Swim");
  await shot(page, "4-year");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("info-banner")).toContainText("Year 1997");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("year-badge")).toHaveText("1997");
});
