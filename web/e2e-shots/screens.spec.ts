// Portfolio screenshots (make screenshots). Opens every screen of the running
// demo as the resident, the owner and a System Admin, and saves one picture
// per screen in docs/screenshots/<phone|desktop>/. Run `make demo` first so
// the charts have 90 days of history and there are bills.
//
// It only looks: nothing is saved, paid or switched (the admin used here is
// its own account, made with the same command a real admin is made with).
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { login, PASSWORD, totp } from "../e2e/helpers";

const OUT = process.env.SHOTS_DIR ?? "../docs/screenshots";
const ADMIN = "screenshots-admin@example.com";

test.describe.configure({ mode: "serial" });

/** Wait until the page has loaded its data and the charts have drawn. */
async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await expect(page.locator("main")).toBeVisible();
  await page.waitForTimeout(1500);
}

async function shot(page: Page, info: TestInfo, name: string): Promise<void> {
  await settle(page);
  const dir = `${OUT}/${info.project.name}`;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/${name}.png` });
}

/** Open a page of the app (paths are relative to /app/). */
async function open(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

async function dark(page: Page, on: boolean): Promise<void> {
  await page.evaluate(
    (value) => localStorage.setItem("aquapulse.theme", value),
    on ? "dark" : "light",
  );
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

const isPhone = (info: TestInfo) => info.project.name === "phone";

test("login page", async ({ page }, info) => {
  await page.goto("login");
  await expect(page.getByRole("button", { name: "Log in" })).toBeVisible();
  await page.waitForTimeout(800);
  const dir = `${OUT}/${info.project.name}`;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/00-login.png` });
});

test("resident screens", async ({ page }, info) => {
  await login(page, "resident01@example.com");
  // The login page has a heading too: wait until the app has left it.
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { level: 1 })).not.toHaveText("Log in");
  const pages: [string, string][] = [
    ["./", "resident-01-my-home"],
    ["usage", "resident-02-usage"],
    ["quality", "resident-03-water-quality"],
    ["bills", "resident-04-bills"],
    ["insights", "resident-05-ai-insights"],
    ["alerts", "resident-06-alerts"],
  ];
  for (const [path, name] of pages) {
    await open(page, path);
    await shot(page, info, name);
  }
  // One bill, opened from the list.
  await open(page, "bills");
  const bill = page.locator('a[href*="/bills/"]').first();
  if (await bill.count()) {
    await bill.click();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await shot(page, info, "resident-07-bill");
  }
  // The same home screen in dark mode.
  await open(page, "./");
  await dark(page, true);
  await shot(page, info, "resident-08-my-home-dark");
  await dark(page, false);
});

test("System Admin screens", async ({ page }, info) => {
  test.skip(isPhone(info), "the admin console is a desktop tool");
  const admins = (...args: string[]) =>
    execFileSync(
      "docker",
      [
        "compose",
        "exec",
        "-T",
        "-e",
        "ADMIN_PASSWORD",
        "api",
        "python",
        "-m",
        "app.admins",
        ...args,
      ],
      {
        cwd: "../",
        env: { ...process.env, ADMIN_PASSWORD: PASSWORD },
        encoding: "utf8",
      },
    );
  admins("add", ADMIN);
  admins("reset-2fa", ADMIN);

  // FR-70: a new admin must turn on two-factor login first.
  await login(page, ADMIN);
  await expect(
    page.getByRole("heading", { name: "Turn on two-factor login" }),
  ).toBeVisible();
  await shot(page, info, "admin-01-two-factor-required");
  const card = page.locator("section", {
    has: page.getByRole("heading", { name: "Two-factor login" }),
  });
  await card.getByLabel("Your password").fill(PASSWORD);
  await card.getByRole("button", { name: "Turn on two-factor login" }).click();
  const secret = (await card.locator("code").textContent())!.trim();
  await card
    .getByLabel("Type the 6-digit code the app shows")
    .fill(totp(secret));
  await card.getByRole("button", { name: "Turn on", exact: true }).click();
  await card.getByRole("button", { name: "I saved them" }).click();

  for (const [path, name] of [
    ["./", "admin-02-system-health"],
    ["owners", "admin-03-owners-and-buildings"],
    ["audit", "admin-04-audit-log"],
  ] as const) {
    await open(page, path);
    await shot(page, info, name);
  }
});

test("owner screens", async ({ page }, info) => {
  // The owner's pages make many requests and the API allows 120 a minute per
  // user: let the phone run's minute pass before the desktop run.
  if (!isPhone(info))
    await new Promise((resolve) => setTimeout(resolve, 60_000));
  await login(page, "owner@example.com");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "AquaPulse Demo Tower",
  );

  const pages: [string, string][] = isPhone(info)
    ? [
        ["./", "owner-01-overview"],
        ["homes", "owner-02-homes"],
        ["tank", "owner-04-tank-and-pump"],
        ["ai", "owner-10-ai"],
      ]
    : [
        ["./", "owner-01-overview"],
        ["homes", "owner-02-homes"],
        ["tank", "owner-04-tank-and-pump"],
        ["devices", "owner-05-devices"],
        ["tariffs", "owner-06-tariffs"],
        ["bills", "owner-07-bills"],
        ["alert-rules", "owner-08-alert-rules"],
        ["settings", "owner-09-building-settings"],
        ["ai", "owner-10-ai"],
        ["alerts", "owner-11-alerts"],
      ];
  for (const [path, name] of pages) {
    await open(page, path);
    await shot(page, info, name);
    // One home's details, opened from the list of homes.
    if (path === "homes") {
      await page.locator('a[href*="/homes/"]').first().click();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await shot(page, info, "owner-03-home-details");
    }
  }
  if (!isPhone(info)) {
    await open(page, "./");
    await dark(page, true);
    await shot(page, info, "owner-12-overview-dark");
    await dark(page, false);
  }
});
