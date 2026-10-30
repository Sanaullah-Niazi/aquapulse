// Numbers and dates for people. One place, so every screen looks the same.
import i18n from "./i18n";

const locale = () => i18n.language || "en";

export function litres(value: number | string | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "-";
  return `${Number(value).toLocaleString(locale(), { maximumFractionDigits: digits })} L`;
}

export function number(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString(locale(), { dateStyle: "medium", timeStyle: "short" });
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "-";
  // "2026-10-30" is a calendar day: show it as it is, whatever the browser's time zone.
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(locale(), { dateStyle: "medium" });
}

export function month(iso: string): string {
  const [y, m] = iso.slice(0, 7).split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(locale(), { month: "long", year: "numeric" });
}

/** "3 min ago", "2 h ago" */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "-";
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  const rtf = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
  if (seconds < 60) return rtf.format(-seconds, "second");
  if (seconds < 3600) return rtf.format(-Math.round(seconds / 60), "minute");
  if (seconds < 86_400) return rtf.format(-Math.round(seconds / 3600), "hour");
  return rtf.format(-Math.round(seconds / 86_400), "day");
}

/** Water quality label from pH (FR-48). The limits are the building's own
 * alert settings (default: safe from 6.5 to 8.5). "Check" = within 0.3 of a limit. */
export function phLabel(
  ph: number | null | undefined,
  low = 6.5,
  high = 8.5,
): "safe" | "check" | "unsafe" | "unknown" {
  if (ph === null || ph === undefined) return "unknown";
  if (ph < low || ph > high) return "unsafe";
  if (ph < low + 0.3 || ph > high - 0.3) return "check";
  return "safe";
}
