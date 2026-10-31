# web/

The AquaPulse web app: React + TypeScript, built with Vite, installable as a
PWA (FR-53 to FR-57). Served by Caddy at https://localhost:8443/app/ after
`make up`. Guide: [docs/milestones/M8-web-app.md](../docs/milestones/M8-web-app.md).

| Command (from the project folder) | What it does |
|---|---|
| `make web-setup` | Install the packages and the Playwright browser (once) |
| `make web-dev` | Instant-reload dev server at http://localhost:5173/app/ (uses the API from `make up`) |
| `make web-test` | Type check and unit tests (Vitest) |
| `make web-e2e` | Browser tests (Playwright) at 390 px and 1440 px, with axe-core accessibility checks |
| `make web-build` | Build into `dist/` |

```text
src/main.tsx, App.tsx    start, routes by role
src/api.ts, auth.tsx     API calls, tokens (access token in memory, refresh token in localStorage)
src/live.ts              WebSocket with a one-time ticket, reconnects by itself
src/pages/               one file per screen (resident/, shared/)
src/components/          layout, charts, valve switch, small building blocks
src/locales/en.json      every text on screen
public/config.js         settings that can change after the build (where the API is)
e2e/                     Playwright tests
```

The folder the app lives in is set at build time: `VITE_BASE=/water/ pnpm build`
(default `/app/`). The API address is set in `public/config.js` (empty means the
same site).
