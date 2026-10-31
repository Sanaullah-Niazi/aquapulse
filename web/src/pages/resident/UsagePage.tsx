// R-02: my water use per day and per month, against last month and against
// the building's average per person (FR-49). Only my own stay is shown.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { BarsChart } from "../../components/Charts";
import { ErrorBox, Loading, Segmented, Stat } from "../../components/ui";
import * as fmt from "../../format";
import type { Comparison, Consumption } from "../../types";
import { useHomePicker } from "./MyHomePage";

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function UsagePage() {
  const { t } = useTranslation();
  const { homeId, picker } = useHomePicker();
  const [days, setDays] = useState(30);
  const daily = useQuery({
    queryKey: ["usage", homeId, "day", days],
    queryFn: () =>
      api<Consumption>(`/homes/${homeId}/consumption?granularity=day&from=${isoDaysAgo(days - 1)}`),
    enabled: homeId !== null,
  });
  const monthly = useQuery({
    queryKey: ["usage", homeId, "month"],
    queryFn: () =>
      api<Consumption>(`/homes/${homeId}/consumption?granularity=month&from=${isoDaysAgo(364)}`),
    enabled: homeId !== null,
  });
  const comparison = useQuery({
    queryKey: ["comparison", homeId],
    queryFn: () => api<Comparison>(`/homes/${homeId}/comparison`),
    enabled: homeId !== null,
  });

  if (homeId === null) return <p className="notice info">{t("home.noHome")}</p>;
  const c = comparison.data;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("usage.title")}</h1>
        {picker}
      </div>
      <h2>{t("usage.comparison")}</h2>
      {comparison.error ? (
        <ErrorBox error={comparison.error} />
      ) : (
        <div className="grid" style={{ marginBottom: "1rem" }}>
          <Stat label={t("usage.thisMonth")} value={c ? fmt.litres(c.litres) : "…"} />
          <Stat
            label={t("usage.lastMonth")}
            value={c ? (c.previous_month_litres === null ? "-" : fmt.litres(c.previous_month_litres)) : "…"}
            sub={c && c.previous_month_litres === null ? t("usage.notHere") : undefined}
          />
          <Stat
            label={t("usage.perPerson", { people: c?.people_in_home ?? "…" })}
            value={c ? fmt.litres(c.litres_per_person) : "…"}
          />
          <Stat
            label={t("usage.buildingAverage")}
            value={c ? fmt.litres(c.building_litres_per_person) : "…"}
            sub={c && c.building_litres_per_person === null ? t("usage.noAverage") : undefined}
          />
        </div>
      )}

      <section className="card" style={{ marginBottom: "1rem" }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>{t("usage.daily")}</h2>
          <Segmented
            label={t("usage.range")}
            value={days}
            options={[7, 30, 90].map((n) => ({ value: n, label: t("usage.days", { count: n }) }))}
            onChange={setDays}
          />
        </div>
        {daily.isPending ? (
          <Loading />
        ) : daily.error ? (
          <ErrorBox error={daily.error} retry={() => daily.refetch()} />
        ) : (
          <>
            <BarsChart
              title={t("usage.daily")}
              unit="L"
              data={daily.data.points.map((p) => ({ label: fmt.date(p.start), value: Math.round(p.litres) }))}
            />
            <p className="muted small">{t("usage.total", { litres: fmt.litres(daily.data.total_litres) })}</p>
          </>
        )}
      </section>

      <section className="card">
        <h2>{t("usage.monthly")}</h2>
        {monthly.isPending ? (
          <Loading />
        ) : monthly.error ? (
          <ErrorBox error={monthly.error} />
        ) : monthly.data.points.length === 0 ? (
          <p className="muted">{t("usage.none")}</p>
        ) : (
          <BarsChart
            title={t("usage.monthly")}
            unit="L"
            data={monthly.data.points.map((p) => ({ label: fmt.month(p.start), value: Math.round(p.litres) }))}
          />
        )}
      </section>
    </>
  );
}
