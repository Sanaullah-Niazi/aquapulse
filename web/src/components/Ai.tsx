// M10 screens: the forecast with its 80% range and month-end bill (FR-65),
// and AI insights with their reason and evidence (FR-63, FR-64, FR-66).
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import * as fmt from "../format";
import type { Forecast, Insight } from "../types";
import { ErrorBox, Loading, SeverityBadge, Stat } from "./ui";

const axis = { stroke: "var(--muted)", fontSize: 12 };
const tooltipStyle = {
  contentStyle: { background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text)" },
  labelStyle: { color: "var(--muted)" },
};

function dayLabel(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric" });
}

interface Row {
  label: string;
  actual?: number;
  forecast?: number;
  range?: [number, number];
}

/** The last 14 real days, then 7 forecast days with a shaded 80% range. */
export function forecastRows(f: Forecast): Row[] {
  const rows: Row[] = f.recent.map((d) => ({ label: dayLabel(d.day), actual: d.litres }));
  const last = rows.at(-1);
  if (last) last.forecast = last.actual; // joins the two lines
  for (const d of f.days) rows.push({ label: dayLabel(d.day), forecast: d.litres, range: [d.low, d.high] });
  return rows;
}

export function ForecastChart({ forecast }: { forecast: Forecast }) {
  const { t } = useTranslation();
  const rows = forecastRows(forecast);
  const summary = forecast.days
    .map((d) => `${d.day}: ${Math.round(d.litres)} L (${Math.round(d.low)} to ${Math.round(d.high)})`)
    .join(", ");
  return (
    <figure style={{ margin: 0 }}>
      <div className="chart" aria-hidden="true">
        <ResponsiveContainer>
          <ComposedChart accessibilityLayer={false} data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" tick={axis} interval="preserveStartEnd" minTickGap={16} />
            <YAxis tick={axis} />
            <Tooltip
              {...tooltipStyle}
              formatter={(v: number | [number, number], name: string) => [
                Array.isArray(v) ? `${Math.round(v[0])} to ${Math.round(v[1])} L` : `${Math.round(v)} L`,
                t(`ai.series.${name}`),
              ]}
            />
            <Area dataKey="range" stroke="none" fill="var(--chart-2)" fillOpacity={0.25} isAnimationActive={false} />
            <Line dataKey="actual" stroke="var(--chart-1)" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="forecast" stroke="var(--chart-2)" strokeWidth={2} strokeDasharray="5 4" dot={false}
                  isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="small muted">
        {t("ai.legend")}
        <span className="sr-only"> {summary}</span>
      </figcaption>
    </figure>
  );
}

/** The forecast card: chart, month-end litres and the bill estimate. */
export function ForecastCard({ path, title }: { path: string; title: string }) {
  const query = useQuery({ queryKey: ["forecast", path], queryFn: () => api<Forecast>(path), staleTime: 10 * 60_000 });
  return (
    <section className="card" aria-labelledby="forecast-title">
      <h2 id="forecast-title">{title}</h2>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} retry={() => query.refetch()} />
      ) : (
        <ForecastBody f={query.data} />
      )}
    </section>
  );
}

function ForecastBody({ f }: { f: Forecast }) {
  const { t } = useTranslation();
  if (f.history_days === 0) return <p className="muted">{t("ai.noHistory")}</p>;
  const week = f.days.reduce((sum, d) => sum + d.litres, 0);
  return (
    <>
      <div className="grid" style={{ marginBottom: "0.75rem" }}>
        <Stat label={t("ai.next7")} value={fmt.litres(week)} />
        <Stat
          label={t("ai.monthEnd", { day: fmt.date(f.month_end.last_day) })}
          value={fmt.litres(f.month_end.litres)}
          sub={t("ai.between", { low: fmt.litres(f.month_end.low), high: fmt.litres(f.month_end.high) })}
        />
        <Stat
          label={t("ai.bill")}
          value={f.bill.total_text ?? "-"}
          sub={
            f.bill.low_text && f.bill.high_text
              ? t("ai.between", { low: f.bill.low_text, high: f.bill.high_text })
              : undefined
          }
        />
      </div>
      <ForecastChart forecast={f} />
      <p className="small">{f.bill.message}</p>
      <details>
        <summary className="small">{t("ai.how")}</summary>
        <p className="small">
          <strong>{t(`ai.method.${f.method}`)}</strong>. {f.explanation}
        </p>
      </details>
    </>
  );
}

/** A small flow chart of the readings behind a leak or burst alert. */
export function EvidenceChart({ insight }: { insight: Insight }) {
  const { t } = useTranslation();
  const points = insight.evidence.points ?? [];
  if (points.length < 2) return null;
  const time = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const data = points.map(([at, lpm]) => ({ label: time(at), value: lpm }));
  const burst = insight.kind === "burst" ? insight.evidence.burst_lpm : undefined;
  return (
    <figure style={{ margin: "0.5rem 0 0" }}>
      <div className="chart chart-small" aria-hidden="true">
        <ResponsiveContainer>
          <LineChart accessibilityLayer={false} data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" tick={axis} interval="preserveStartEnd" minTickGap={24} />
            <YAxis tick={axis} domain={[0, "auto"]} />
            <Tooltip {...tooltipStyle} formatter={(v: number) => [`${v} L/min`, t("ai.flow")]} />
            {burst !== undefined && <ReferenceLine y={burst} stroke="var(--bad)" strokeDasharray="4 4" />}
            <Line dataKey="value" stroke="var(--chart-1)" dot={false} strokeWidth={2} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="sr-only">
        {t("ai.flow")}: {points.length} readings, lowest {Math.min(...points.map((p) => p[1]))} L/min, highest{" "}
        {Math.max(...points.map((p) => p[1]))} L/min
      </figcaption>
    </figure>
  );
}

export function InsightItem({ insight }: { insight: Insight }) {
  const { t } = useTranslation();
  const top = insight.evidence.top_features ?? [];
  return (
    <li className="card" style={{ listStyle: "none" }}>
      <div className="row" style={{ justifyContent: "space-between", gap: "0.5rem" }}>
        <strong>{insight.title}</strong>
        <SeverityBadge severity={insight.severity} />
      </div>
      <p style={{ margin: "0.4rem 0" }}>{insight.explanation}</p>
      <p className="small muted" style={{ margin: 0 }}>
        {fmt.dateTime(insight.opened_at)}
        {insight.confidence !== null &&
          ` · ${t("ai.confidence", { value: Math.round(insight.confidence * 100) })}`}
        {insight.high_confidence && ` · ${t("ai.sure")}`}
        {insight.resolved_at ? ` · ${t("ai.over")}` : ""}
        {insight.evidence.model_version !== undefined &&
          ` · ${t("ai.modelVersion", { version: insight.evidence.model_version })}`}
      </p>
      <EvidenceChart insight={insight} />
      {top.length > 0 && (
        <ul className="small" style={{ margin: "0.4rem 0 0" }}>
          {top.map((f) => (
            <li key={f.feature}>
              {t(`ai.feature.${f.feature}`)}: {f.value} ({t("ai.normal")} {f.normal})
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function InsightsCard({ homeId }: { homeId: string }) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ["insights", homeId],
    queryFn: () => api<Insight[]>(`/homes/${homeId}/insights`),
  });
  return (
    <section className="card" aria-labelledby="insights-title">
      <h2 id="insights-title">{t("ai.insights")}</h2>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} retry={() => query.refetch()} />
      ) : query.data.length === 0 ? (
        <p className="muted">{t("ai.noInsights")}</p>
      ) : (
        <ul style={{ padding: 0, margin: 0, display: "grid", gap: "0.75rem" }}>
          {query.data.slice(0, 10).map((i) => (
            <InsightItem key={i.alert_id} insight={i} />
          ))}
        </ul>
      )}
    </section>
  );
}
