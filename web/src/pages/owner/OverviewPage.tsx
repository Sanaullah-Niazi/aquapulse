// O-01: the owner's building at a glance (FR-42). Everything updates live.
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { ErrorBox, Loading, SeverityBadge, Stat } from "../../components/ui";
import * as fmt from "../../format";
import type { Severity } from "../../types";
import { NoBuilding, useBuilding, useOverview } from "./building";
import { HomesTable } from "./HomesPage";

export function OverviewPage() {
  const { t } = useTranslation();
  const { buildingId, picker } = useBuilding();
  const overview = useOverview(buildingId);
  if (buildingId === null) return <NoBuilding />;
  if (overview.isPending) return <Loading />;
  if (overview.error) return <ErrorBox error={overview.error} retry={() => overview.refetch()} />;
  const o = overview.data;
  const change =
    o.litres_last_month_to_date > 0
      ? Math.round((o.litres_month / o.litres_last_month_to_date - 1) * 100)
      : null;
  const offline = o.homes_with_device - o.homes_online;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{o.name}</h1>
        {picker}
      </div>
      <div className="grid" style={{ marginBottom: "1rem" }}>
        <Stat label={t("owner.today")} value={fmt.litres(o.litres_today)} />
        <Stat
          label={t("owner.month")}
          value={fmt.litres(o.litres_month)}
          sub={
            change === null
              ? t("owner.noLastMonth")
              : t("owner.vsLastMonth", {
                  change: `${change > 0 ? "+" : ""}${change}%`,
                  litres: fmt.litres(o.litres_last_month_to_date),
                })
          }
        />
        <Stat
          label={t("owner.flowNow")}
          value={`${fmt.number(o.flow_lpm)} L/min`}
          sub={t("owner.homesRunning", { count: o.rows.filter((r) => r.flow_lpm > 0).length })}
        />
        <Stat
          label={t("owner.homesOnline")}
          value={`${o.homes_online} / ${o.homes_with_device}`}
          sub={offline > 0 ? <span className="error">⚠ {t("owner.offline", { count: offline })}</span> : t("owner.allOnline")}
        />
        <Stat
          label={t("owner.valves")}
          value={t("owner.valvesClosed", { count: o.valves_closed })}
          sub={t("owner.valvesLocked", { count: o.valves_locked })}
        />
        <Stat
          label={t("owner.unpaid")}
          value={o.unpaid_text}
          sub={<Link to="/bills?show=unpaid">{t("owner.unpaidBills", { count: o.unpaid_bills })}</Link>}
        />
      </div>

      <div className="grid-2" style={{ marginBottom: "1rem" }}>
        <section className="card" aria-labelledby="alerts-title">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 id="alerts-title">{t("owner.openAlerts")}</h2>
            <Link to="/alerts">{t("home.seeAll")}</Link>
          </div>
          <div className="row">
            {(["critical", "warning", "info"] as Severity[]).map((s) => (
              <span key={s} className="row">
                <SeverityBadge severity={s} /> <strong>{o.alerts[s] ?? 0}</strong>
              </span>
            ))}
          </div>
        </section>
        <section className="card" aria-labelledby="tank-title">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 id="tank-title">{t("tank.title")}</h2>
            {o.tank && <Link to="/tank">{t("owner.open")}</Link>}
          </div>
          {o.tank ? (
            <div className="row">
              <TankGauge percent={o.tank.level_pct} />
              <div>
                <p style={{ margin: 0 }}>
                  {t(o.tank.pump_on ? "tank.pumpOn" : "tank.pumpOff")} · {t(`tank.modes.${o.tank.mode}`)}
                </p>
                {!o.tank.online && <p className="error">⚠ {t("tank.offline")}</p>}
              </div>
            </div>
          ) : (
            <p className="muted">{t("tank.none")}</p>
          )}
        </section>
      </div>

      <section className="card" aria-labelledby="ai-title" style={{ marginBottom: "1rem" }}>
        <h2 id="ai-title">{t("owner.insights")}</h2>
        <p className="muted" style={{ margin: 0 }}>{t("owner.insightsLater")}</p>
      </section>

      <section className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2>{t("owner.homes")}</h2>
          <Link to="/homes">{t("owner.allHomes")}</Link>
        </div>
        <HomesTable overview={o} />
      </section>
    </>
  );
}

/** A tank drawn as a bar that fills up, with the number in words too. */
export function TankGauge({ percent, size = "small" }: { percent: number | null; size?: "small" | "large" }) {
  const { t } = useTranslation();
  const value = percent ?? 0;
  const height = size === "large" ? 200 : 90;
  return (
    <div className="row" style={{ alignItems: "flex-end" }}>
      <div
        className="tank-gauge"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        aria-label={t("tank.level")}
        style={{ height, width: size === "large" ? 90 : 48 }}
      >
        <div className="fill" style={{ height: `${value}%` }} />
      </div>
      <strong style={{ fontSize: size === "large" ? "2rem" : "1.5rem" }}>
        {percent === null ? t("tank.noReading") : `${Math.round(value)}%`}
      </strong>
    </div>
  );
}
