// Where the API is. Read from public/config.js at start-up, so the same build
// can talk to the server on your Mac or to the public one (FR-56).

declare global {
  interface Window {
    AQUAPULSE_CONFIG?: { apiUrl?: string };
  }
}

const apiUrl = (window.AQUAPULSE_CONFIG?.apiUrl ?? "").replace(/\/$/, "");

/** e.g. "/api/v1" (same site) or "https://water-api.example.com/api/v1". */
export const API_BASE = `${apiUrl}/api/v1`;

/** The live WebSocket address for a one-time ticket (FR-25). */
export function websocketUrl(ticket: string): string {
  const http = apiUrl || window.location.origin;
  return `${http.replace(/^http/, "ws")}/api/v1/ws?ticket=${encodeURIComponent(ticket)}`;
}

/** The folder the app is served from, e.g. "/app/" (from vite.config.ts). */
export const BASE_PATH = import.meta.env.BASE_URL;
