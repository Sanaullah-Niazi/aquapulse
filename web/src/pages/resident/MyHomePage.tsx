// R-01: my home at a glance (FR-48), with the water on/off button (FR-51).
// Everything updates live, without reloading.
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { api } from "../../api";
import { useMe } from "../../auth";
import { AlertList } from "../../components/AlertList";
import { ErrorBox, Loading, QualityBadge, Stat } from "../../components/ui";
import { ValveControl } from "../../components/ValveControl";
import * as fmt from "../../format";
import { useHomeLive, useMyHomeId } from "../../hooks";
import type { Alert, BillEstimate, Consumption } from "../../types";

export function useHomePicker() {
  const { t } = useTranslation();
  const me = useMe();
  const [homeId, setHomeId] = useMyHomeId();
  const picker =
    me.home_ids.length > 1 ? (
      <select aria-label={t("home.title")} value={homeId ?? ""} style={{ width: "auto" }}
              onChange={(e) => setHomeId(e.target.value)}>
        {me.home_ids.map((id, n) => (
          <option key={id} value={id}>
            {t("home.title")} {n + 1}
          </option>
        ))}
      </select>
    ) : null;
  return { homeId, picker };
}

export function MyHomePage() {
  const { t } = useTranslation();
  const { homeId, picker } = useHomePicker();
  const live = useHomeLive(homeId);
  const today = useQuery({
    queryKey: ["usage-today", homeId],
    // Today and this month in one call: daily totals since the 1st (building time).
    queryFn: () => {
      const now = new Date();
      const first = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
      return api<Consumption>(`/homes/${homeId}/consumption?granularity=day&from=${first}`);
    },
    enabled: homeId !== null,
  });
  const estimate = useQuery({
    queryKey: ["estimate", homeId],
    queryFn: () => api<BillEstimate>(`/homes/${homeId}/bill-estimate`),
    enabled: homeId !== null,
  });
  const alerts = useQuery({
    queryKey: ["alerts", "home", homeId],
    queryFn: () => api<Alert[]>(`/alerts?home_id=${homeId}&limit=3`),
    enabled: homeId !== null,
  });

  if (homeId === null)
    return (
      <>
        <h1>{t("home.title")}</h1>
        <p className="notice info">{t("home.noHome")}</p>
      </>
    );
  if (live.isPending) return <Loading />;
  if (live.error) return <ErrorBox error={live.error} retry={() => live.refetch()} />;

  const data = live.data;
  const reading = data.reading;
  const quality = fmt.phLabel(reading?.ph, data.ph_safe_low, data.ph_safe_high);
  // The last daily total is today's, if any water was used today.
  const last = today.data?.points.at(-1);
  const todayLitres = last && last.start.slice(0, 10) === today.data?.to ? last.litres : 0;
  const running = (reading?.flow_lpm ?? 0) > 0.05;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1 style={{ marginBottom: ".25rem" }}>{t("home.title")}</h1>
        {picker}
      </div>
      <p className="muted">{t("home.unit", { unit: data.unit_no, building: data.building_name })}</p>
      {data.device_id && !data.online && (
        <p className="notice warn" role="status">
          {t("home.deviceOffline", { when: fmt.ago(data.last_seen_at) })}
        </p>
      )}
      <div className="grid" style={{ marginBottom: "1rem" }}>
        <Stat label={t("home.today")} value={today.data ? fmt.litres(todayLitres) : "…"} />
        <Stat label={t("home.month")} value={today.data ? fmt.litres(today.data.total_litres) : "…"} />
        <Stat
          label={t("home.flow")}
          value={
            <span className="row" style={{ fontSize: "1.1rem" }}>
              <span className={`dot ${running ? "live" : ""}`} aria-hidden="true" />
              {running ? t("home.running", { lpm: fmt.number(reading?.flow_lpm) }) : t("home.notRunning")}
            </span>
          }
        />
        <Stat
          label={t("home.quality")}
          value={<span>pH {fmt.number(reading?.ph)}</span>}
          sub={<QualityBadge label={quality} />}
        />
        <Stat label={t("home.temperature")} value={reading ? `${fmt.number(reading.temp_c)} °C` : "-"} />
        <Stat
          label={t("home.estimate")}
          value={estimate.data?.total_text ?? "-"}
          sub={estimate.data?.message}
        />
      </div>
      <div className="grid-2">
        <section className="card" aria-labelledby="valve-title">
          <h2 id="valve-title">{t("valve.title")}</h2>
          <ValveControl live={data} unit={data.unit_no} />
        </section>
        <section className="card" aria-labelledby="alerts-title">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 id="alerts-title">{t("home.latestAlerts")}</h2>
            <Link to="/alerts">{t("home.seeAll")}</Link>
          </div>
          {alerts.data && alerts.data.length === 0 ? (
            <p className="muted">{t("home.noAlerts")}</p>
          ) : (
            <AlertList alerts={alerts.data ?? []} compact />
          )}
        </section>
      </div>
    </>
  );
}
