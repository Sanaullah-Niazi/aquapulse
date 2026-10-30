// A resident's day in the app (FR-48 to FR-53): every screen loads real data
// from the running stack, fits a phone screen and passes the accessibility check.
import { expect, test } from "@playwright/test";
import { expectAccessible, expectNoSideScroll, login } from "../helpers";

// Each test uses its own demo resident: one account clicking this fast would
// hit the API's limit of 120 requests a minute per person (FR-72).
let next = 0;
test.beforeEach(async ({ page }) => {
  next = (next % 8) + 1;
  await login(page, `resident0${next}@example.com`);
  await expect(page.getByRole("heading", { name: "My home" })).toBeVisible();
});

test("my home shows live numbers", async ({ page }) => {
  await expect(page.getByText(/^Unit \d+, /)).toBeVisible();
  await expect(page.getByText("Used today")).toBeVisible();
  await expect(page.getByText(/^pH \d/)).toBeVisible();
  await expect(page.getByText("Not a bill.")).toBeVisible();
  // The live connection (WebSocket) is up.
  await expect(page.getByRole("status").filter({ hasText: "Live" })).toBeVisible();
  await expectNoSideScroll(page);
  await expectAccessible(page);
});

for (const [menu, heading, check] of [
  ["My usage", "My usage", "Litres per day"],
  ["Water quality", "Water quality", "Drinking water should be between"],
  ["My bills", "My bills", null],
  ["Alerts", "Alerts", null],
  ["Profile", "Profile", "Two-factor login"],
  ["Notifications", "Notifications", "Never email me about"],
] as const) {
  test(`${menu} page`, async ({ page, isMobile, viewport }) => {
    if (isMobile || (viewport && viewport.width < 760)) {
      await page.getByRole("button", { name: "Menu" }).click(); // the phone menu
    }
    await page.getByRole("navigation").getByRole("link", { name: menu, exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    if (check) await expect(page.getByText(check).first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expectNoSideScroll(page);
    await expectAccessible(page);
  });
}

test("nothing scrolls sideways on a small phone (360 px)", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  for (const path of ["", "usage", "quality", "bills", "alerts", "profile", "notifications"]) {
    await page.goto(path || "./");
    await page.waitForLoadState("networkidle");
    await expectNoSideScroll(page);
  }
});

test("notification choices are saved", async ({ page }) => {
  await page.goto("notifications");
  const box = page.getByRole("checkbox", { name: "Sensor stuck" });
  const before = await box.isChecked();
  await box.setChecked(!before);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Sensor stuck" })).toBeChecked({ checked: !before });
  await page.getByRole("checkbox", { name: "Sensor stuck" }).setChecked(before); // put it back
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
});

test("the live pH chart gets new readings without reloading (FR-54)", async ({ page }) => {
  await page.goto("quality");
  await expect(page.getByRole("button", { name: "Live (1 h)" })).toHaveAttribute("aria-pressed", "true");
  const caption = page.locator("figcaption").first();
  await expect(caption).toContainText("latest");
  const before = await caption.textContent();
  // The simulator sends a reading every 10 s; it reaches the chart over the WebSocket.
  await expect(caption).not.toHaveText(before!, { timeout: 15_000 });
  await expect(caption).toContainText(/^pH: \d+ points/);
});
