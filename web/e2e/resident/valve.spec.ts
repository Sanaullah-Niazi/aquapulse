// FR-51: a resident turns their water off and on. The simulator's valve
// really moves and confirms it (the same messages as a real ESP32).
import { expect, test } from "@playwright/test";
import { login } from "../helpers";

test("turn the water off and on again", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "one valve, so only once");
  test.setTimeout(90_000);
  await login(page, "resident03@example.com");
  const supply = page.locator("section", { has: page.getByRole("heading", { name: "Water supply" }) });
  await expect(supply.getByText("Water is ON")).toBeVisible();

  await supply.getByRole("button", { name: "Turn water off" }).click();
  await expect(supply.getByText(/Turn off the water in unit 03\?/)).toBeVisible(); // asks first
  await supply.getByRole("button", { name: "Yes, do it" }).click();
  await expect(supply.getByText("Done. The valve confirmed it.")).toBeVisible({ timeout: 30_000 });
  await expect(supply.getByText("Water is OFF")).toBeVisible();

  await supply.getByRole("button", { name: "Turn water on" }).click();
  await supply.getByRole("button", { name: "Yes, do it" }).click();
  await expect(supply.getByText("Done. The valve confirmed it.")).toBeVisible({ timeout: 30_000 });
  await expect(supply.getByText("Water is ON")).toBeVisible();
});
