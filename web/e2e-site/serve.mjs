// A tiny static web server that behaves like GitHub Pages, for the website
// check (make web-site-check): web/site/ is served at http://localhost:8099/water/,
// and any missing file gets the 404.html page with status 404.
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";

const ROOT = new URL("../site/", import.meta.url).pathname;
const BASE = process.env.SITE_BASE ?? "/water/";
const PORT = Number(process.env.SITE_PORT ?? 8099);
const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".webmanifest": "application/manifest+json",
  ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2",
};

function send(res, status, file) {
  res.writeHead(status, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream" });
  createReadStream(file).pipe(res);
}

createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (`${path}/` === BASE) { // like GitHub Pages: /water opens the folder /water/
    res.writeHead(301, { Location: BASE });
    return res.end();
  }
  if (path.startsWith(BASE)) {
    let file = normalize(join(ROOT, path.slice(BASE.length)));
    if (file.startsWith(ROOT) && existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
    if (file.startsWith(ROOT) && existsSync(file)) return send(res, 200, file);
  }
  send(res, 404, join(ROOT, "404.html")); // like the website's own 404 page
}).listen(PORT, () => console.log(`Serving web/site at http://localhost:${PORT}${BASE}`));
