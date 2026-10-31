// The owner's screens (FR-42 to FR-47, FR-30): every page opens, fits a phone,
// passes the accessibility check, and the main actions really work.
// One login for the whole file: the API allows each user 120 requests a minute.
// Nothing here changes what the resident tests rely on: the lock test uses
// unit 10 and puts everything back, and no bill is issued or tariff saved.
import { expect, test, type Page } from "@playwright/test";
import { expectAccessible, expectNoSideScroll, login } from "../helpers";

test.describe.configure({ mode: "serial" });

let page: Page;

test.beforeAll(async ({ browser }, info) => {
  // The phone run just used most of the owner's 120 requests for this minute:
  // let the window pass before the desktop run starts.
  if (info.project.name !== info.config.projects[0].name) {
    test.setTimeout(120_000);
    await new Promise((resolve) => setTimeout(resolve, 60_000));
  }
  // Its own context with this project's settings (phone or desktop size).
  const context = await browser.newContext(info.project.use);
  page = await context.newPage();
  await login(page, "owner@example.com");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("AquaPulse Demo Tower");
});

test.afterAll(async () => {
  await page.context().close();
});

const PAGES: [string, string][] = [
  ["Overview", "AquaPulse Demo Tower"],
  ["Homes", "Homes"],
  ["Tank and pump", "Tank and pump"],
  ["Devices", "Devices"],
  ["Bills", "Bills"],
  ["Tariffs", "Tariffs"],
  ["Alert rules", "Alert rules"],
  ["Building settings", "Building settings"],
];

test("every owner page opens from the menu, fits the screen and is accessible", async () => {
  for (const [link, heading] of PAGES) await test.step(link, async () => {
    if (await page.getByRole("button", { name: "Menu" }).isVisible()) {
      await page.getByRole("button", { name: "Menu" }).click(); // phone: the menu is folded away
    }
    await page.getByRole("navigation").getByRole("link", { name: link, exact: true }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(heading);
    await expect(page.getByText("Loading…")).toHaveCount(0);
    await expectNoSideScroll(page);
    await expectAccessible(page);
  });
});

test("the overview adds up the homes and shows the tank", async () => {
  await page.goto("./");
  await expect(page.getByText("Meters online", { exact: true })).toBeVisible();
  await expect(page.getByText(/^\d+ \/ 10$/)).toBeVisible(); // 10 demo homes
  await expect(page.getByRole("meter", { name: "Water level" })).toBeVisible();
  // The AI card only says what the AI page shows: no made-up numbers here.
  await expect(page.getByText(/water forecast and bill estimate/)).toBeVisible();
});

test("homes can be searched and sorted", async () => {
  await page.goto("homes");
  const rows = page.locator("tbody tr");
  await expect(rows).toHaveCount(10);
  await page.getByLabel("Search by unit or resident").fill("unit 07");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("07");
  await page.getByLabel("Search by unit or resident").fill("");
  const today = page.getByRole("columnheader", { name: /Today/ });
  if (await page.getByLabel("Sort by").isVisible()) {
    await page.getByLabel("Sort by").selectOption("today"); // phone: a dropdown
  } else {
    await today.getByRole("button").click();
  }
  await expect(today).toHaveAttribute("aria-sort", "ascending");
});

test("a valve lock needs a reason of 10 characters, and can be undone", async () => {
  await page.goto("homes");
  await page.getByRole("link", { name: "Unit 10" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Unit 10");
  await page.getByRole("button", { name: /Lock the valve/ }).click();
  await page.getByLabel("Keep the water").selectOption("open");
  const reason = page.getByLabel("Reason");
  const confirm = page.getByRole("button", { name: "Lock the valve", exact: true });
  await reason.fill("too short");
  await expect(confirm).toBeDisabled();
  await reason.fill("Browser test: lock for a moment");
  await confirm.click();
  await expect(page.getByText(/Locked/).first()).toBeVisible({ timeout: 15_000 });
  // Undo it, so the demo home is free again.
  await page.getByRole("button", { name: /Unlock the valve/ }).click();
  await page.getByLabel("Reason").fill("Browser test: done, unlock again");
  await page.getByRole("button", { name: "Unlock the valve", exact: true }).click();
  await expect(page.getByRole("button", { name: /Lock the valve/ })).toBeVisible({ timeout: 15_000 });
});

test("the tank asks before switching to manual", async () => {
  await page.goto("tank");
  const toManual = page.getByRole("button", { name: "Switch to manual" });
  if (await toManual.isVisible()) {
    await toManual.click();
    await expect(page.getByText(/does not start by itself/)).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByText(/does not start by itself/)).toHaveCount(0);
  }
  await expect(page.getByRole("heading", { name: "Pump hours per day" })).toBeVisible();
});

test("the device list shows every meter and the tank", async () => {
  await page.goto("devices");
  // Each working device (10 meters + the tank) can get a new password.
  await expect(page.getByRole("button", { name: "New password" })).toHaveCount(11);
});

test("the tariff editor stops gaps and shows a live example bill", async () => {
  await page.goto("tariffs");
  await page.getByRole("button", { name: "Make a new version" }).click();
  const form = page.getByRole("form", { name: "New tariff version" });
  // Slab 2 may not end below where it starts (5,000 L).
  await form.getByLabel("Slab 2 ends at (litres)").fill("4000");
  await expect(form.getByRole("alert")).toContainText("Slab 2");
  await expect(form.getByRole("button", { name: "Save new version" })).toBeDisabled();
  await form.getByLabel("Slab 2 ends at (litres)").fill("15000");
  await expect(form.getByRole("alert")).toHaveCount(0);
  // The example comes from the API's own bill maths: 5,000 L at 60 per 1,000 L.
  await form.getByLabel("Litres used in a month").fill("5000");
  await expect(page.getByTestId("preview-total")).toHaveText(/300\.00/);
  await form.getByLabel("Litres used in a month").fill("6000");
  await expect(page.getByTestId("preview-total")).toHaveText(/420\.00/); // + 1,000 L at 120
  await form.getByRole("button", { name: "Cancel" }).click();
  await expect(form).toHaveCount(0);
});

test("bills: make the month's drafts, list unpaid, download a CSV", async () => {
  await page.goto("bills"); // last month is picked already: the newest finished month
  await page.getByRole("button", { name: "Make draft bills" }).click();
  await expect(page.getByRole("status").filter({ hasText: /recalculated/ })).toBeVisible();
  // With `make demo-data` there are readings and so bills; a fresh stack (CI) may have none.
  await expect(page.locator("tbody tr").first().or(page.getByText("No bills for this month yet."))).toBeVisible();

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Bills (CSV)" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/\.csv$/);

  await page.getByRole("button", { name: "Unpaid" }).click();
  await expect(page).toHaveURL(/show=unpaid/);
  await expect(page.getByText(/unpaid bill|Every bill is paid/).first()).toBeVisible();
});

test("an owner opens a bill and sees their actions", async () => {
  await page.goto("bills");
  const open = page.getByRole("link", { name: /^Open/ }).first();
  await expect(open.or(page.getByText("No bills for this month yet."))).toBeVisible();
  test.skip(!(await open.isVisible()), "no bills yet: run make demo-data first");
  await open.click();
  const actions = page.getByRole("region", { name: "Owner actions" });
  await expect(actions).toBeVisible();
  await actions.getByRole("button", { name: "Void this bill" }).click();
  await expect(actions.getByRole("button", { name: "Yes, void it" })).toBeDisabled(); // needs a reason
  await actions.getByRole("button", { name: "Cancel" }).click();
});

test("alert rules and settings save", async () => {
  await page.goto("alert-rules");
  await page.getByRole("button", { name: "Save rules" }).click();
  await expect(page.getByText("Rules saved.")).toBeVisible();
  await page.goto("settings");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
});
