// The System Admin console (FR-55) and its two-factor rule (FR-70), in the real app.
//
// The test uses its own admin, e2e-admin@example.com, made with the same
// command a real admin is made with (make admin), so the demo admin is never
// changed. Each run first resets that admin's two-factor login
// (make admin-reset-2fa), so the run starts like a brand-new admin.
import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { expectAccessible, expectNoSideScroll, login, PASSWORD, totp } from "../helpers";

const ADMIN = "e2e-admin@example.com";

/** `python -m app.admins ...` inside the running API container (what make admin runs). */
function admins(...args: string[]): string {
  return execFileSync(
    "docker", ["compose", "exec", "-T", "-e", "ADMIN_PASSWORD", "api", "python", "-m", "app.admins", ...args],
    { cwd: "..", env: { ...process.env, ADMIN_PASSWORD: PASSWORD }, encoding: "utf8" },
  );
}

test("a new System Admin must turn on two-factor login, then uses the console", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "one admin account, so only once");
  test.setTimeout(90_000);
  admins("add", ADMIN);
  admins("reset-2fa", ADMIN);

  // FR-70: the password logs in, but only the setup page opens.
  await login(page, ADMIN);
  await expect(page.getByRole("heading", { name: "Turn on two-factor login" })).toBeVisible();
  await page.goto("audit");
  await expect(page.getByRole("heading", { name: "Turn on two-factor login" })).toBeVisible();
  await expectAccessible(page);

  const card = page.locator("section", { has: page.getByRole("heading", { name: "Two-factor login" }) });
  await card.getByLabel("Your password").fill(PASSWORD);
  await card.getByRole("button", { name: "Turn on two-factor login" }).click();
  const secret = (await card.locator("code").textContent())!.trim();
  await card.getByLabel("Type the 6-digit code the app shows").fill(totp(secret));
  await card.getByRole("button", { name: "Turn on", exact: true }).click();
  await expect(card.getByRole("heading", { name: "Your recovery codes" })).toBeVisible();
  await card.getByRole("button", { name: "I saved them" }).click();

  // The console opens. System health first (FR-55).
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "System health" })).toBeVisible();
  for (const service of ["Database (TimescaleDB)", "MQTT broker (Mosquitto)", "Ingestor (device messages in)",
                         "Worker (bills, alerts, emails)"]) {
    await expect(page.getByText(service)).toBeVisible();
  }
  await expect(page.getByText("Everything is running.")).toBeVisible();
  // The backup service made its first backup when the stack started (NFR-18).
  await expect(page.getByRole("heading", { name: "Last backup" })).toBeVisible();
  await expect(page.getByText("No backup has been made yet")).toHaveCount(0);
  await expectNoSideScroll(page);
  await expectAccessible(page);

  // Owners and buildings: add an owner, disable and enable them, add a building.
  const stamp = Date.now().toString(36);
  const email = `e2e-owner-${stamp}@example.com`;
  const name = `E2E Owner ${stamp}`;
  await page.getByRole("link", { name: "Owners and buildings" }).first().click();
  await expect(page.getByRole("heading", { name: "Owners and buildings", level: 1 })).toBeVisible();
  await expect(page.getByText("AquaPulse Demo Tower")).toBeVisible();
  const newOwner = page.locator("form", { has: page.getByRole("heading", { name: "Add an owner" }) });
  await newOwner.getByLabel("Full name").fill(name);
  await newOwner.getByLabel("Email").fill(email);
  await newOwner.getByLabel("First password", { exact: true }).fill(`${PASSWORD}-${stamp}`);
  await newOwner.getByRole("button", { name: "Add owner" }).click();
  await expect(page.getByText(`Owner ${email} added.`)).toBeVisible();
  const row = page.getByRole("row", { name: new RegExp(name) });
  await row.getByRole("button", { name: `Disable ${name}` }).click();
  await row.getByRole("button", { name: "Yes, disable" }).click();
  await expect(row.getByText("Disabled")).toBeVisible();
  await row.getByRole("button", { name: "Enable" }).click();
  await expect(row.getByText("Active")).toBeVisible();

  const newBuilding = page.locator("form", { has: page.getByRole("heading", { name: "Add a building" }) });
  await newBuilding.getByLabel("Building name").fill(`E2E Tower ${stamp}`);
  await newBuilding.getByLabel("Owner", { exact: true }).selectOption({ label: name });
  await newBuilding.getByRole("button", { name: "Add building" }).click();
  await expect(page.getByText(`Building E2E Tower ${stamp} added.`)).toBeVisible();
  await expect(page.getByLabel(`Owner of E2E Tower ${stamp}`)).toHaveValue(/.+/);
  await expectNoSideScroll(page);
  await expectAccessible(page);

  // Audit log: what we just did is there, and the chain is untouched (FR-28).
  await page.getByRole("link", { name: "Audit log" }).first().click();
  const action = page.getByLabel("Action (exact, e.g. auth.login)");
  await action.fill("admin.owner_created");
  await action.press("Enter");
  await expect(page.locator("td code", { hasText: "admin.owner_created" }).first()).toBeVisible();
  await page.getByRole("button", { name: "Check the chain now" }).click();
  await expect(page.getByText(/^Untouched: all \d+ entries checked/)).toBeVisible();
  await expectNoSideScroll(page);
  await expectAccessible(page);
});
