// FR-56: the app built for a folder of your own website (make web-site
// BASE=/water/), served like GitHub Pages serves it, talking to the API on
// another address (CORS). Run with: make web-site-check
import { expect, test } from "@playwright/test";
import { login } from "../e2e/helpers";

test("it can be installed as an app (PWA) from the /water/ folder", async ({ page, context }) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  // The service worker (offline start) is running for this folder.
  const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
  expect(scope).toBe("http://localhost:8099/water/");
  const manifest = await (await page.request.get("manifest.webmanifest")).json();
  expect([manifest.start_url, manifest.scope]).toEqual(["/water/", "/water/"]);
  // Chrome's own installability check, the one behind its "Install app" button.
  const cdp = await context.newCDPSession(page);
  const { installabilityErrors } = await cdp.send("Page.getInstallabilityErrors");
  expect(installabilityErrors).toEqual([]);
});

test.describe("without the service worker, like a first visit", () => {
  test.use({ serviceWorkers: "block" });

  test("log in, live data and a link straight to a page", async ({ page }) => {
    await login(page, "resident03@example.com");
    await expect(page.getByRole("heading", { name: "My home" })).toBeVisible();
    await expect(page).toHaveURL(/^http:\/\/localhost:8099\/water\/?$/);
    // The live connection (WebSocket) to the API on its own address works.
    await expect(page.getByRole("banner").getByText("Live", { exact: true })).toBeVisible({ timeout: 15_000 });

    // /water/bills is not a file on the website: its 404 page sends the
    // visitor on to the app, which opens the right page.
    const answer = await page.request.get("bills");
    expect(answer.status()).toBe(404);
    await page.goto("bills");
    await expect(page.getByRole("heading", { name: "My bills" })).toBeVisible();
    await expect(page).toHaveURL("http://localhost:8099/water/bills");
  });
});
