// Shared steps for the browser tests.
import { createHmac } from "node:crypto";
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** The demo password from .env (make seed made the demo accounts with it). */
export const PASSWORD = process.env.DEMO_PASSWORD ?? "";
if (!PASSWORD) throw new Error("Set DEMO_PASSWORD (make web-e2e reads it from .env)");

export async function login(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.goto("login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
}

/** The page fits the screen: nothing to scroll sideways (FR-53). */
export async function expectNoSideScroll(page: Page): Promise<void> {
  const extra = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(extra, "the page scrolls sideways").toBeLessThanOrEqual(0);
}

/** No serious or critical accessibility problems (axe-core, WCAG 2.1 AA; NFR-21). */
export async function expectAccessible(page: Page): Promise<void> {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.help} (${v.nodes.length}x)`)).toEqual([]);
}

/** The 6-digit code an authenticator app shows (RFC 6238: SHA-1, 30 s steps). */
export function totp(secret: string, stepOffset = 0): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/=+$/, "").toUpperCase()) {
    bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  }
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30) + stepOffset));
  const mac = createHmac("sha1", key).update(counter).digest();
  const at = mac[mac.length - 1] & 0xf;
  const number = (mac.readUInt32BE(at) & 0x7fffffff) % 1_000_000;
  return number.toString().padStart(6, "0");
}
