import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

// Runs against the demo database, which has a few shows no channel carries (Recess, Gravity
// Falls, Over the Garden Wall). Each browser project uses its own channel number and name.
// Undo is global (it reverts the last admin write, whoever made it), so specs must not run in
// parallel against one API: playwright.config.ts runs its webkit project after its chromium one.
const shot = (page: Page, name: string) => page.screenshot({ path: `test-results/screens/${name}.png` });

interface Row {
  id: string;
  name: string;
}

async function removeLeftovers(request: APIRequestContext, name: string) {
  const list = (await (await request.get("/api/admin/channels")).json()) as { channels: Row[] };
  for (const c of list.channels.filter((r) => r.name === name)) {
    await request.delete(`/api/admin/channels/${c.id}`);
  }
}

// Every admin write rebuilds the schedules, and both browsers may share one API, so waits are long.
const expectSlow = expect.configure({ timeout: 30_000 });
test.setTimeout(120_000);

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

test("create a channel on the phone, see it in the guide, then undo", async ({ browser, page, request }, info) => {
  const number = info.project.name.endsWith("webkit") ? "51" : "50";
  const name = `Brandon's Toons ${info.project.name}`;
  await removeLeftovers(request, name);

  await page.goto("/admin");
  await expectSlow(page.getByTestId("admin-channels")).toBeVisible();
  await shot(page, `admin-1-channels-${info.project.name}`);
  // No horizontal scroll at phone width.
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.getByRole("button", { name: "+ New channel" }).click();
  await page.getByLabel("Number").fill(number);
  await page.getByLabel("Call sign").fill("toons");
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByRole("switch").check();

  await page.getByRole("button", { name: "+ Add show" }).click();
  const picker = page.getByTestId("show-picker");
  await expectSlow(picker.getByText("Not on any channel")).toBeVisible();
  await page.getByLabel("Search the library").fill("recess");
  await picker.getByRole("button", { name: "Add Recess" }).click();
  await page.getByLabel("Search the library").fill("gravity");
  await picker.getByRole("button", { name: "Add Gravity Falls" }).click();
  await page.getByRole("button", { name: "Done" }).click();
  await expectSlow(page.getByTestId("editor-shows")).toContainText("Recess");
  await expectSlow(page.getByTestId("editor-shows")).toContainText("Gravity Falls");
  // Gravity Falls really debuted in 2012. Library channels no longer lift debut dates (M2), so
  // it is flagged, and it airs in 2000 only with a pretend premiere on this channel.
  const gravity = page.getByTestId("editor-shows").getByRole("button", { name: /Gravity Falls/ });
  await expect(gravity).toContainText("won't air");
  await gravity.click();
  await expect(page.getByLabel("Pretend premiere")).toBeFocused();
  await page.getByLabel("Pretend premiere").fill("1999-06-15");
  await expect(page.getByTestId("editor-shows")).toContainText("Pretend premiere Jun 1999 · really Jun 2012");
  await shot(page, `admin-2-editor-${info.project.name}`);

  // Preview the week before saving: both shows, back to back, from the same engine.
  await page.getByRole("button", { name: "Preview week" }).click();
  const day = page.getByTestId("preview-day");
  await expectSlow(day).toContainText("Recess");
  await expectSlow(day).toContainText("Gravity Falls");
  await shot(page, `admin-3-preview-${info.project.name}`);
  await page.getByRole("button", { name: "Close" }).click();

  await page.getByRole("button", { name: "Save" }).click();
  const toast = page.getByRole("status");
  await expectSlow(toast).toContainText(`Created ${name}`);
  await expectSlow(page.getByTestId("admin-channels")).toContainText(name);
  // Keep the undo toast up (it pauses while hovered) while we check the TV.
  await toast.hover();

  // On the TV right away: tune it by number and find it in the guide.
  const tv = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await tv.addInitScript(() => {
    window.localStorage.setItem("rc.year", "2000");
    window.localStorage.removeItem("rc.channel");
  });
  await tv.goto("/");
  await tv.keyboard.press("Enter");
  await expectSlow(tv.getByTestId("info-banner")).toBeVisible();
  await tv.keyboard.type(number);
  await expectSlow(tv.getByTestId("banner-number")).toHaveText(number);
  await expectSlow(tv.getByTestId("banner-title")).toHaveText(/Recess|Gravity Falls|Brandon's Toons/);
  await tv.keyboard.press("Enter");
  await expectSlow(tv.getByTestId("guide")).toBeVisible();
  await expectSlow(tv.getByTestId(`guide-row-custom-brandon-s-toons-${info.project.name}`)).toBeVisible();
  await tv.screenshot({ path: `test-results/screens/admin-4-guide-${info.project.name}.png` });
  await tv.close();

  // Undo: the channel is gone again.
  await toast.getByRole("button", { name: "Undo" }).click();
  await expectSlow(page.getByRole("status")).toContainText("Restored");
  await expectSlow(page.getByTestId("admin-channels")).not.toContainText(name);
  await removeLeftovers(request, name);
});

interface GuideBody {
  channels: { channel: { id: string }; slots: { show: string | null; transplant: boolean; air_date: string | null }[] }[];
}

test("give a YAML channel's show a pretend premiere, see the mark in the guide, then undo", async ({ page, request }) => {
  const bear = async () => {
    const guide = (await (await request.get("/api/guide?year=2000&date=06-05&hours=24")).json()) as GuideBody;
    return (guide.channels.find((r) => r.channel.id === "disney")?.slots ?? []).filter(
      (s) => s.show === "bear-in-the-big-blue-house",
    );
  };
  const before = await bear();
  expect(before.every((s) => !s.transplant)).toBe(true);

  await page.goto("/admin");
  await page.getByTestId("admin-channel-disney").click();
  const shows = page.getByTestId("editor-shows");
  await expectSlow(shows).toContainText("Bear in the Big Blue House");
  // Read-only rows: no chevrons (M2 D26), but a pretend premiere field each.
  await expect(shows.getByText("⌄")).toHaveCount(0);
  const row = shows.getByRole("listitem").filter({ hasText: "Bear in the Big Blue House" });
  await row.getByLabel("Pretend premiere").fill("1999-01-04");
  await row.getByRole("button", { name: "Set" }).click();
  const toast = page.getByRole("status");
  await expectSlow(toast).toContainText("Set the pretend premiere of Bear in the Big Blue House");
  await toast.hover();
  await expect(row).toContainText("Pretend premiere Jan 1999");
  await expect(row).toContainText("S1E1 airs Jan 4, 1999");
  await shot(page, "admin-5-premiere");

  // The guide follows on the next read: Bear carries the mark and its shifted dates on Disney
  // Channel, and only there.
  await expectSlow.poll(async () => (await bear()).some((s) => s.transplant)).toBe(true);
  for (const s of await bear()) {
    expect(s.transplant).toBe(true);
    expect((s.air_date ?? "") >= "1999-01-04").toBe(true);
  }

  await toast.getByRole("button", { name: "Undo" }).click();
  await expectSlow(page.getByRole("status")).toContainText("Restored");
  await expectSlow.poll(async () => (await bear()).some((s) => s.transplant)).toBe(false);
});

test("the Media tab shows conversion progress or says it isn't available", async ({ page }) => {
  await page.goto("/admin");
  await page.getByRole("button", { name: "Media" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Media" })).toBeFocused();
  await expectSlow(
    page.getByText(/isn't available on this server yet|Nothing to convert|files ·/).first(),
  ).toBeVisible();
  // No horizontal scroll at phone width.
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("week-preview day chips fit at 360 px", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/admin");
  // A draft with one show; it is never saved.
  await page.getByRole("button", { name: "+ New channel" }).click();
  await page.getByLabel("Number").fill("59");
  await page.getByLabel("Name", { exact: true }).fill("Chip check");
  await page.getByRole("switch").check();
  await page.getByRole("button", { name: "+ Add show" }).click();
  await page.getByLabel("Search the library").fill("recess");
  await page.getByTestId("show-picker").getByRole("button", { name: "Add Recess" }).click();
  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("button", { name: "Preview week" }).click();
  const chips = page.locator(".adm-chips");
  await expectSlow(chips).toBeVisible();
  const box = await chips.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
  expect(box.scroll).toBeLessThanOrEqual(box.client);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
});
