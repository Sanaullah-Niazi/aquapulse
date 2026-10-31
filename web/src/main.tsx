// The start of the web app.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { BASE_PATH } from "./config";
import "./i18n";
import "./styles.css";
import "./theme";

const queries = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000, // live events refresh what changes; no need to ask again and again
      retry: 1,
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
