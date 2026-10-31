// Browser tests (M8): a real Chromium clicks through the app running in
// Docker (make up), at phone and desktop size. Run with:  make web-e2e
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  // One test at a time: all tests share the same running stack and demo accounts.
  workers: 1,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "https://localhost:8443/app/",
    ignoreHTTPSErrors: true, // the local certificate (make certs) is not trusted by this browser
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "phone", use: { viewport: { width: 390, height: 844 }, hasTouch: true } },
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
  ],
});
