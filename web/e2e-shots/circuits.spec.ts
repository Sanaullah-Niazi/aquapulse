// Pictures of the two ESP32 circuits (M11), made from the same Wokwi files
// the simulator uses (firmware/wokwi/*/diagram.json). The parts are Wokwi's
// own drawings (the @wokwi/elements library), and every wire joins exactly
// the pins listed in the diagram. Only the placement is spread out so the
// wires are easy to follow. Part of make screenshots; needs no running stack.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";

const OUT = process.env.SHOTS_DIR ?? "../docs/screenshots";
const require = createRequire(import.meta.url);
const BUNDLE = require.resolve("@wokwi/elements/dist/wokwi-elements.bundle.js");

type Place = Record<string, [number, number]>;
const NODES: { dir: string; file: string; title: string; place: Place }[] = [
  {
    dir: "home-node",
    file: "hardware-01-home-node",
    title: "Home node (ESP32): water flow, pH, temperature and the valve motor",
    place: {
      esp: [330, 260],
      flow: [40, 40],
      note1: [20, 14],
      ph: [175, 40],
      note2: [195, 14],
      temp: [320, 46],
      pullup: [410, 70],
      valveOpen: [540, 110],
      valveClose: [620, 110],
    },
  },
  {
    dir: "tank-node",
    file: "hardware-02-tank-node",
    title: "Tank node (ESP32): tank level, sump switch and the pump relay",
    place: {
      esp: [300, 260],
      level: [40, 40],
      note1: [20, 14],
      sump: [400, 80],
      note2: [370, 54],
      pump: [520, 110],
    },
  },
];

for (const node of NODES) {
  test(`circuit: ${node.dir}`, async ({ page }, info) => {
    test.skip(info.project.name !== "desktop", "one picture per circuit");
    const diagram = readFileSync(
      `../firmware/wokwi/${node.dir}/diagram.json`,
      "utf8",
    );
    await page.setContent(`<!doctype html><html><head><style>
      body { margin: 0; background: #f6f8fa; font-family: system-ui, sans-serif; }
      h1 { font-size: 22px; margin: 24px 32px 0; color: #1f2328; }
      p { margin: 6px 32px 0; color: #57606a; font-size: 14px; }
      #board { position: relative; margin: 8px 24px 24px; zoom: 1.7; }
      #board > * { position: absolute; }
      .note { font-size: 9px; color: #57606a; white-space: nowrap; }
      .chip { background: #24292f; color: #fff; border-radius: 3px; font: 600 8px system-ui;
              display: flex; align-items: center; justify-content: center; }
      .label { font-size: 8px; color: #57606a; white-space: nowrap; }
    </style></head><body><h1></h1>
      <p>Drawn from the Wokwi simulator files in firmware/wokwi. Same parts, same pins.</p>
      <div id="board"></div></body></html>`);
    await page.addScriptTag({ path: BUNDLE });
    await page.evaluate(
      async ({ diagramText, title, place }) => {
        type Pin = { name: string; x: number; y: number };
        type Part = { type: string; id: string; attrs: Record<string, string> };
        const d = JSON.parse(diagramText) as {
          parts: Part[];
          connections: [string, string, string, string[]][];
        };
        document.querySelector("h1")!.textContent = title;
        const board = document.getElementById("board")!;
        const pins: Record<string, { x: number; y: number }> = {};
        const add = (el: HTMLElement, x: number, y: number) => {
          el.style.left = `${x}px`;
          el.style.top = `${y}px`;
          board.appendChild(el);
        };
        const placed: [Part, HTMLElement, Pin[]][] = [];
        for (const part of d.parts) {
          const [x, y] = place[part.id] ?? [0, 0];
          let el: HTMLElement;
          let own: Pin[] = [];
          if (part.type === "wokwi-text") {
            el = document.createElement("div");
            el.className = "note";
            el.textContent = part.attrs.text;
          } else if (part.type === "wokwi-ds18b20") {
            // Not in the public parts library: drawn as a labelled chip with its 3 pins.
            el = document.createElement("div");
            el.className = "chip";
            el.style.width = "44px";
            el.style.height = "56px";
            el.textContent = "DS18B20";
            own = [
              { name: "GND", x: 8, y: 56 },
              { name: "DQ", x: 22, y: 56 },
              { name: "VCC", x: 36, y: 56 },
            ];
          } else {
            await customElements.whenDefined(part.type);
            el = document.createElement(part.type);
            for (const [k, v] of Object.entries(part.attrs)) {
              if (k !== "label") el.setAttribute(k, v);
            }
            if (part.attrs.label) {
              const label = document.createElement("div");
              label.className = "label";
              label.textContent = part.attrs.label;
              add(label, x - 4, y - 12);
            }
          }
          add(el, x, y);
          placed.push([part, el, own]);
        }
        await new Promise((r) => setTimeout(r, 300));
        let width = 0;
        let height = 0;
        for (const [part, el, own] of placed) {
          const list = own.length
            ? own
            : ((el as unknown as { pinInfo?: Pin[] }).pinInfo ?? []);
          for (const p of list) {
            pins[`${part.id}:${p.name}`] = {
              x: el.offsetLeft + p.x,
              y: el.offsetTop + p.y,
            };
          }
          width = Math.max(width, el.offsetLeft + el.offsetWidth);
          height = Math.max(height, el.offsetTop + el.offsetHeight);
        }
        const esp = placed.find(([p]) => p.id === "esp")![1];
        const espLeft = esp.offsetLeft;
        const espRight = esp.offsetLeft + esp.offsetWidth;
        const espBottom = esp.offsetTop + esp.offsetHeight;
        const espTop = esp.offsetTop;
        const colors: Record<string, string> = { black: "#24292f" };
        const lines: string[] = [];
        let left = 0;
        let right = 0;
        let level = 0;
        // Each wire gets its own lane, so no two wires run on top of each other.
        const lane = (x: number) =>
          x < espLeft + 50
            ? espLeft - 10 - 7 * left++
            : espRight + 10 + 7 * right++;
        for (const [from, to, color] of d.connections) {
          if (!color) continue; // the serial monitor has no wire
          let a = pins[from];
          let b = pins[to];
          if (!a || !b) continue;
          let path: number[][];
          const aEsp = from.startsWith("esp:");
          const bEsp = to.startsWith("esp:");
          if (aEsp && bEsp) {
            // A wire from one ESP32 pin to another: around the bottom of the board.
            const xa = lane(a.x);
            const xb = lane(b.x);
            const yb = espBottom + 10;
            path = [
              [a.x, a.y],
              [xa, a.y],
              [xa, yb],
              [xb, yb],
              [xb, b.y],
              [b.x, b.y],
            ];
          } else if (aEsp || bEsp) {
            if (aEsp) [a, b] = [b, a]; // a = the part, b = the ESP32 pin
            const x = lane(b.x);
            // Across above the board, never over it.
            const y = Math.min(a.y + 12 + 5 * level++, espTop - 6);
            path = [
              [a.x, a.y],
              [a.x, y],
              [x, y],
              [x, b.y],
              [b.x, b.y],
            ];
          } else {
            const y = Math.max(a.y, b.y) + 10;
            path = [
              [a.x, a.y],
              [a.x, y],
              [b.x, y],
              [b.x, b.y],
            ];
          }
          lines.push(
            `<polyline points="${path.map((p) => p.join(",")).join(" ")}" fill="none" ` +
              `stroke="${colors[color] ?? color}" stroke-width="1.6" stroke-linejoin="round"/>` +
              `<circle cx="${path[0][0]}" cy="${path[0][1]}" r="1.8" fill="${colors[color] ?? color}"/>` +
              `<circle cx="${b.x}" cy="${b.y}" r="1.8" fill="${colors[color] ?? color}"/>`,
          );
          for (const [px, py] of path) {
            width = Math.max(width, px + 10);
            height = Math.max(height, py + 10);
          }
        }
        const svg = document.createElement("div");
        svg.innerHTML = `<svg width="${width}" height="${height}" style="overflow:visible">${lines.join("")}</svg>`;
        svg.style.pointerEvents = "none";
        add(svg, 0, 0);
        board.style.width = `${width}px`;
        board.style.height = `${height}px`;
      },
      { diagramText: diagram, title: node.title, place: node.place },
    );
    await expect(page.locator("#board polyline").first()).toBeAttached();
    const size = await page.evaluate(() => ({
      w: document.documentElement.scrollWidth,
      h: document.documentElement.scrollHeight,
    }));
    await page.setViewportSize({
      width: Math.max(size.w, 900),
      height: size.h,
    });
    mkdirSync(`${OUT}/hardware`, { recursive: true });
    await page.screenshot({
      path: `${OUT}/hardware/${node.file}.png`,
      fullPage: true,
    });
  });
}
