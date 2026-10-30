// Logging in and out (FR-01) and two-factor login (FR-70) in the real app.
import { expect, request, test } from "@playwright/test";
import { expectAccessible, expectNoSideScroll, login, PASSWORD, totp } from "./helpers";

test("the login page works and is accessible", async ({ page }) => {
  await page.goto("./");
  await expect(page).toHaveURL(/\/login$/); // logged out: sent to the login page
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  await expectNoSideScroll(page);
  await expectAccessible(page);
});

test("wrong password, then right password, then log out", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "once is enough (failed logins are rate limited)");
  await login(page, "resident02@example.com", "not the right password");
  await expect(page.getByRole("alert")).toBeVisible();
  await login(page, "resident02@example.com");
  await expect(page.getByRole("heading", { name: "My home" })).toBeVisible();
  await page.getByRole("link", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("bills");
  await expect(page).toHaveURL(/\/login$/); // really logged out
});

test("two-factor login: turn on, log in with a code, turn off", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "one account, so only once");
  const email = "resident09@example.com";
  let secret = "";
  let recovery: string[] = [];
  try {
    await login(page, email);
    await expect(page.getByRole("heading", { name: "My home" })).toBeVisible();
    await page.goto("profile"); // a fresh page load: the session carries on
    const card = page.locator("section", { has: page.getByRole("heading", { name: "Two-factor login" }) });
    await card.getByLabel("Your password").fill(PASSWORD);
    await card.getByRole("button", { name: "Turn on two-factor login" }).click();
    await expect(card.locator(".qr svg")).toBeVisible(); // the QR code to scan
    secret = (await card.locator("code").textContent())!.trim();
    await card.getByLabel("Type the 6-digit code the app shows").fill(totp(secret));
    await card.getByRole("button", { name: "Turn on", exact: true }).click();
    await expect(card.getByRole("heading", { name: "Your recovery codes" })).toBeVisible();
    recovery = (await card.locator(".codes span").allTextContents()).map((c) => c.trim());
    expect(recovery).toHaveLength(10);
    await card.getByRole("button", { name: "I saved them" }).click();
    await expect(card.getByText(/^On\./)).toBeVisible();

    // Log out and in again: now the app asks for the code.
    await page.getByRole("link", { name: "Log out" }).click();
    await login(page, email);
    await expect(page.getByRole("heading", { name: "Two-factor code" })).toBeVisible();
    await page.getByLabel("Code").fill(recovery[0]); // a recovery code works too
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "My home" })).toBeVisible();

    // Turn it off again: password and a fresh code.
    await page.goto("profile");
    await card.getByLabel("Your password").fill(PASSWORD);
    await card.getByLabel("Type the 6-digit code the app shows").fill(totp(secret, 1));
    await card.getByRole("button", { name: "Turn off two-factor login" }).click();
    await expect(card.getByText(/^Off\./)).toBeVisible();
  } finally {
    // If something above failed, leave the demo account as it was (2FA off).
    if (secret) await switchOff(info.project.use.baseURL!, email, secret, recovery);
  }
});

async function switchOff(base: string, email: string, secret: string, recovery: string[]) {
  const api = await request.newContext({ baseURL: new URL("/api/v1/", base).href, ignoreHTTPSErrors: true });
  let r = await api.post("auth/login", { data: { email, password: PASSWORD } });
  if (r.ok()) return; // already off
  r = await api.post("auth/login", { data: { email, password: PASSWORD, code: recovery.at(-1) } });
  if (!r.ok()) return;
  const headers = { Authorization: `Bearer ${(await r.json()).access_token}` };
  for (const step of [1, 0, -1]) {
    r = await api.post("me/2fa/disable", { headers, data: { password: PASSWORD, code: totp(secret, step) } });
    if (r.ok()) break;
  }
  await api.dispose();
}
