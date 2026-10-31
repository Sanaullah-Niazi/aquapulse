// Frames of docs/animation/system.html (make animation): opens the page in
// Chromium, calls its render(t) for every frame and saves one PNG per frame.
// Usage: node scripts/animation-frames.mjs <page.html> <out dir> [fps]
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const [page, out, fps = "10"] = process.argv.slice(2);
const browser = await chromium.launch();
const tab = await browser.newPage({ viewport: { width: 1200, height: 675 } });
await tab.goto(`file://${resolve(page)}`);
const loop = await tab.evaluate(() => window.LOOP);
mkdirSync(out, { recursive: true });
const frames = Math.round(loop * Number(fps));
for (let i = 0; i < frames; i++) {
  await tab.evaluate((t) => window.render(t), i / Number(fps));
  await tab.screenshot({ path: `${out}/f${String(i).padStart(4, "0")}.png` });
}
await browser.close();
console.log(`${frames} frames in ${out}`);
