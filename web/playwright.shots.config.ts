// Portfolio screenshots: a real Chromium opens every screen of the running
// demo (make demo) and saves a picture of it in docs/screenshots/.
// Nothing is drawn or edited: these are the real pages with the demo data.
// Run with:  make screenshots
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e-shots",
  // One at a time: the pages share the demo accounts and their rate limit.
  workers: 1,
  fullyParallel: false,
  timeout: 240_000,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "https://localhost:8443/app/",
    ignoreHTTPSErrors: true, // the local certificate (make certs)
    colorScheme: "light",
  },
  projects: [
    // Sharp pictures: 3x for the phone (1170 x 2532), 2x for the desktop (2880 x 1800).
    {
      name: "phone",
      use: {
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 3,
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: "desktop",
      use: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 },
    },
  ],
});
