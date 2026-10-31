// Unit tests for the M10 screens' helpers (run with: pnpm test).
import { describe, expect, it } from "vitest";
import { forecastRows } from "./components/Ai";
import { scores } from "./pages/owner/AiPage";
import type { AiModel, Forecast } from "./types";

const forecast: Forecast = {
  method: "holt_winters",
  history_days: 30,
  explanation: "",
  recent: [
    { day: "2026-10-29", litres: 400 },
    { day: "2026-10-30", litres: 420 },
  ],
  days: [
    { day: "2026-10-31", litres: 500, low: 450, high: 560 },
    { day: "2026-11-01", litres: 520, low: 460, high: 590 },
  ],
  month_end: { last_day: "2026-10-31", litres: 9000, low: 8800, high: 9300 },
  bill: { currency_code: "PKR", total: "780.00", total_text: "Rs 780.00", low_text: null, high_text: null, message: "" },
};

describe("forecastRows", () => {
  it("puts the real days first, then the forecast days with their range", () => {
    const rows = forecastRows(forecast);
    expect(rows).toHaveLength(4);
    expect(rows[0]).toMatchObject({ actual: 400 });
    expect(rows[0].forecast).toBeUndefined();
    // The last real day also starts the forecast line, so the two lines join.
    expect(rows[1]).toMatchObject({ actual: 420, forecast: 420 });
    expect(rows[2]).toMatchObject({ forecast: 500, range: [450, 560] });
    expect(rows[3].actual).toBeUndefined();
  });
});

describe("scores", () => {
  const base = { id: "1", version: 1, trained_at: "", data_from: "", data_to: "", rows: 1, params: {}, live: true, note: "" };
  it("shows forecast errors as percentages", () => {
    const m = { ...base, kind: "forecast", metrics: { method_mape: 0.157, naive_mape: 0.195, coverage: 0.85 } } as AiModel;
    expect(scores(m)).toBe("MAPE 16% · naive 20% · in range 85%");
  });
  it("shows the unusual-use check", () => {
    const m = { ...base, kind: "unusual_use", metrics: { doubled_recall: 0.9, false_rate: 0.02 } } as AiModel;
    expect(scores(m)).toBe("doubled days caught 90% · real days flagged 2%");
  });
});
