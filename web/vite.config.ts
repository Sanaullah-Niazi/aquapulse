/// <reference types="vitest/config" />
// Vite builds the app into dist/: plain HTML, JS and CSS files that any web
// server can host (FR-56). `base` is the folder the app lives in: /app/ on
// your Mac (Caddy), or e.g. /water/ on your own website:  VITE_BASE=/water/ pnpm build
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

const base = process.env.VITE_BASE ?? "/app/";

export default defineConfig({
  base,
  plugins: [
    react(),
    // PWA (FR-56): a manifest (name, icons, colours) makes the app installable,
    // and a service worker keeps the app's own files so it opens offline.
    // API answers are never cached: water data must always be fresh.
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "config.js"],
      manifest: {
        name: "AquaPulse",
        short_name: "AquaPulse",
        description: "Smart water for your building: live use, valves, bills and alerts",
        theme_color: "#0b6e99",
        background_color: "#ffffff",
        display: "standalone",
        start_url: base,
        scope: base,
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        navigateFallback: `${base}index.html`,
        navigateFallbackDenylist: [/\/api\//],
        // config.js is read on every start (it may change after a build).
        globIgnores: ["config.js"],
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.endsWith("/config.js"),
            handler: "NetworkFirst",
          },
        ],
      },
    }),
  ],
  build: {
    rollupOptions: {
      // Libraries in their own files: they change rarely, so browsers keep them cached.
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router", "@tanstack/react-query"],
          charts: ["recharts"],
        },
      },
    },
  },
  server: {
    // `pnpm dev`: the API runs in Docker behind Caddy (make up).
    proxy: {
      "/api": { target: "https://localhost:8443", secure: false, ws: true, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
