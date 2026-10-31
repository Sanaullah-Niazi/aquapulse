// R-03: pH and temperature over time, with the safe pH range drawn on the chart.
// "Live" shows the last hour, reading by reading; new readings arrive over the
// WebSocket and are added to the chart at once, without asking the API (FR-54).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { LinesChart } from "../../components/Charts";
import { ErrorBox, Loading, QualityBadge, Segmented } from "../../components/ui";
import * as fmt from "../../format";
import { useHomeLive } from "../../hooks";
import { useLiveEvents } from "../../live";
import type { Reading } from "../../types";
import { useHomePicker } from "./MyHomePage";

const RANGES = {
  live: { hours: 1, every: null }, // every reading (one each 10 s)
  "24h": { hours: 24, every: 600 }, // 10-minute averages
  "7d": { hours: 168, every: 3600 }, // hourly averages
} as const;
type Range = keyof typeof RANGES;

export function QualityPage() {
  const { t } = useTranslation();
  const { homeId, picker } = useHomePicker();
  const [range, setRange] = useState<Range>("live");
  const live = useHomeLive(homeId);
  const queries = useQueryClient();
  const key = ["quality", homeId, range];
  const history = useQuery({
    queryKey: key,
    queryFn: () => {
      const { hours, every } = RANGES[range];
      const from = new Date(Date.now() - hours * 3600_000).toISOString();
      const averaged = every === null ? "" : `&every=${every}`;
      return api<Reading[]>(`/homes/${homeId}/telemetry?from=${encodeURIComponent(from)}${averaged}`);
    },
    enabled: homeId !== null,
    refetchInterval: range === "live" ? false : 60_000,
  });
  useLiveEvents((event) => {
    if (range !== "live" || event.type !== "telemetry" || event.homeId !== homeId) return;
    if (event.ph === undefined || event.tempC === undefined || !event.time) return;
    const reading: Reading = {
      time: event.time,
      total_l: event.totalL ?? 0,
      delta_l: event.deltaL ?? 0,
      flow_lpm: event.flowLpm ?? 0,
      ph: event.ph,
      temp_c: event.tempC,
      valve: event.valve ?? "",
      rssi: 0,
    };
    const hourAgo = Date.now() - 3600_000;
    queries.setQueryData<Reading[]>(key, (old) =>
      old && !old.some((r) => r.time === reading.time)
        ? [...old.filter((r) => new Date(r.time).getTime() >= hourAgo), reading]
        : old,
    );
  });
  if (homeId === null) return <p className="notice info">{t("home.noHome")}</p>;
  const low = live.data?.ph_safe_low ?? 6.5;
  const high = live.data?.ph_safe_high ?? 8.5;
  const label = (r: Reading) =>
    range === "live"
      ? new Date(r.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
      : range === "24h"
        ? new Date(r.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
        : new Date(r.time).toLocaleDateString([], { weekday: "short", hour: "2-digit" });

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("quality.title")}</h1>
        {picker}
      </div>
      <div className="card" style={{ marginBottom: "1rem" }}>
        <div className="row">
          <strong>pH {fmt.number(live.data?.reading?.ph)}</strong>
          <QualityBadge label={fmt.phLabel(live.data?.reading?.ph, low, high)} />
        </div>
        <p className="muted small" style={{ marginTop: ".5rem" }}>
          {t("quality.explain", { low, high })}
        </p>
      </div>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <Segmented
          label={t("usage.range")}
          value={range}
          options={(Object.keys(RANGES) as Range[]).map((r) => ({ value: r, label: t(`quality.ranges.${r}`) }))}
          onChange={setRange}
        />
      </div>
      {history.isPending ? (
        <Loading />
      ) : history.error ? (
        <ErrorBox error={history.error} retry={() => history.refetch()} />
      ) : (
        <div className="grid-2">
          <section className="card">
            <h2>{t("quality.phTitle")}</h2>
            <LinesChart
              title="pH"
              unit=""
              low={low}
              high={high}
              domain={[Math.min(5, low - 0.5), Math.max(10, high + 0.5)]}
              data={history.data.map((r) => ({ label: label(r), value: r.ph }))}
            />
          </section>
          <section className="card">
            <h2>{t("quality.tempTitle")}</h2>
            <LinesChart
              title={t("quality.tempTitle")}
              unit="°C"
              data={history.data.map((r) => ({ label: label(r), value: r.temp_c }))}
            />
          </section>
        </div>
      )}
    </>
  );
}
