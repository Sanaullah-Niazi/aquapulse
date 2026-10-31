// Light / dark / "like my device" (FR-57). The choice is remembered in this browser.
import { useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "aquapulse.theme";

function apply(choice: ThemeChoice) {
  const dark =
    choice === "dark" ||
    (choice === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

function saved(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(saved);
  useEffect(() => {
    apply(choice);
    try {
      localStorage.setItem(KEY, choice);
    } catch {
      // private mode: just not remembered
    }
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const follow = () => choice === "system" && apply("system");
    media?.addEventListener("change", follow);
    return () => media?.removeEventListener("change", follow);
  }, [choice]);
  return [choice, setChoice];
}

apply(saved()); // before the first paint, so there is no white flash in dark mode
