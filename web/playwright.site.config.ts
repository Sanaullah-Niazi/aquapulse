// The website check (FR-56): the app built for a sub-folder of a website
// (make web-site), served like GitHub Pages serves it. Run with: make web-site-check
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e-site",
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://localhost:8099/water/",
    ignoreHTTPSErrors: true, // the API on your Mac uses the local certificate
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: "node e2e-site/serve.mjs",
    url: "http://localhost:8099/water/",
    reuseExistingServer: false,
  },
});
