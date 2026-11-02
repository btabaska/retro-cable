import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

// Runs against the demo database, which has a few shows no channel carries (Recess, Gravity
// Falls, Over the Garden Wall). Each browser project uses its own channel number and name.
// Undo is global (it reverts the last admin write, whoever made it), so specs must not run in
// parallel against one API: playwright.config.ts uses one worker in CI.
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
  const number = info.project.name === "webkit" ? "51" : "50";
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
