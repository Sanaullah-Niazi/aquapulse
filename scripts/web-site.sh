#!/usr/bin/env bash
# ---------------------------------------------------------------
# Build the web app for your own website (FR-56).
#
#   make web-site BASE=/water/ API=https://water-api.example.com
#
# BASE: the folder on your website the app will live in, e.g. /water/
#       for https://you.github.io/water/. Starts and ends with "/".
# API:  the public address of your AquaPulse server (no /api/v1 at the end).
#       You can change it later in site/config.js without building again.
#
# The result is the folder web/site/. Copy everything in it into that folder
# of your website. See docs/guides/host-on-your-website.md.
# ---------------------------------------------------------------
set -euo pipefail

BASE="${1:-}"
API="${2:-}"

if [[ ! "$BASE" =~ ^/([A-Za-z0-9._-]+/)*$ ]]; then
  echo "BASE must be a folder that starts and ends with /, e.g. BASE=/water/" >&2
  exit 1
fi
if [[ ! "$API" =~ ^https://[^/\"]+$ ]]; then
  echo "API must be the server's https address with no path, e.g. API=https://water-api.example.com" >&2
  echo "(Your website uses https, so the browser only allows an https server.)" >&2
  exit 1
fi

cd "$(dirname "$0")/../web"
rm -rf site
# Built into web/site, so the copy that make up serves (web/dist) is untouched.
VITE_BASE="$BASE" pnpm exec vite build --outDir site --emptyOutDir

# Where the app finds the server, read when it starts (src/config.ts).
cat > site/config.js <<EOF
// Settings read when the app starts (FR-56). Made by: make web-site
// apiUrl: the public address of your AquaPulse server. Change it here if the
// server moves; no new build is needed.
window.AQUAPULSE_CONFIG = {
  apiUrl: "$API",
};
EOF

# GitHub Pages: open files as they are (no Jekyll processing).
touch site/.nojekyll

# A link straight to a page inside the app (e.g. ${BASE}bills) is not a real
# file, so a static host answers "404". This page sends the visitor to the
# app with the page in ?to=, and the app opens it (src/main.tsx). It only
# helps if it is the website's own 404.html (in the website's top folder).
cat > site/404.html <<EOF
<!doctype html>
<meta charset="utf-8">
<title>Opening AquaPulse...</title>
<script>
  // Made by make web-site. A link into the AquaPulse app at $BASE opens the app.
  var base = "$BASE";
  var path = location.pathname;
  if (path.indexOf(base) === 0 && path !== base) {
    location.replace(base + "?to=" + encodeURIComponent(path.slice(base.length) + location.search));
  } else {
    document.write("<p>Page not found.</p>");
  }
</script>
EOF

echo ""
echo "Done: web/site/ is the app for https://<your website>$BASE"
echo "  server address in web/site/config.js: $API"
echo "Next: copy everything in web/site/ into the ${BASE#/} folder of your website."
echo "      Guide: docs/guides/host-on-your-website.md"
