// The start of the web app.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { ApiError } from "./api";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { BASE_PATH } from "./config";
import "./i18n";
import "./styles.css";
import "./theme";

// A link straight to a page of the app on a static website comes back from
// the website's 404 page as ?to=bills (scripts/web-site.sh). Open that page.
const to = new URLSearchParams(window.location.search).get("to");
if (to !== null) window.history.replaceState(null, "", BASE_PATH + to.replace(/^\/+/, ""));

const queries = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000, // live events refresh what changes; no need to ask again and again
      // Ask again only when trying again can help: the API was busy (429),
      // down (5xx) or unreachable. Waits as long as the API asks (Retry-After).
      retry: (count, error) => count < 3 && (!(error instanceof ApiError) || error.status === 429 || error.status >= 500),
      retryDelay: (count, error) => {
        const asked = error instanceof ApiError ? Number(error.headers.get("Retry-After")) : 0;
        return Math.min(asked > 0 ? asked * 1000 : 1000 * 2 ** count, 30_000);
      },
      refetchOnWindowFocus: true,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      <BrowserRouter basename={BASE_PATH.replace(/\/$/, "")}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
