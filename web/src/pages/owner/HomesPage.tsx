// O-02: all homes in a searchable, sortable list (FR-43). A click opens the home.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { ErrorBox, Loading, QualityBadge } from "../../components/ui";
import * as fmt from "../../format";
import type { Overview, OverviewRow } from "../../types";
import { NoBuilding, useBuilding, useOverview } from "./building";

type Column = "unit" | "resident" | "today" | "month" | "valve" | "ph" | "temp" | "online" | "alerts";

const SORT: Record<Column, (r: OverviewRow) => string | number> = {
  unit: (r) => r.unit_no,
  resident: (r) => r.residents.join(", ").toLowerCase(),
  today: (r) => r.litres_today,
  month: (r) => r.litres_month,
  valve: (r) => (r.locked ? "z" : "") + (r.valve ?? ""),
  ph: (r) => r.ph ?? -1,
  temp: (r) => r.temp_c ?? -100,
  online: (r) => Number(r.online),
  alerts: (r) => r.open_alerts,
};

export function HomesPage() {
  const { t } = useTranslation();
  const { buildingId, picker } = useBuilding();
  const overview = useOverview(buildingId);
  if (buildingId === null) return <NoBuilding />;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("owner.homes")}</h1>
        {picker}
      </div>
      {overview.isPending ? (
        <Loading />
      ) : overview.error ? (
        <ErrorBox error={overview.error} retry={() => overview.refetch()} />
      ) : (
        <section className="card">
          <HomesTable overview={overview.data} searchable />
        </section>
      )}
    </>
  );
}

export function HomesTable({ overview, searchable = false }: { overview: Overview; searchable?: boolean }) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ by: Column; up: boolean }>({ by: "unit", up: true });
  const words = search.trim().toLowerCase();
  const rows = overview.rows
    .filter(
      (r) =>
        !words ||
        r.unit_no.toLowerCase().includes(words) ||
        r.residents.some((name) => name.toLowerCase().includes(words)),
    )
    .sort((a, b) => {
      const x = SORT[sort.by](a);
      const y = SORT[sort.by](b);
      const order = typeof x === "number" ? x - (y as number) : String(x).localeCompare(String(y), undefined, { numeric: true });
      return sort.up ? order : -order;
    });

  const header = (column: Column, label: string, num = false) => (
    <th
      className={num ? "num" : undefined}
      aria-sort={sort.by === column ? (sort.up ? "ascending" : "descending") : "none"}
    >
      {searchable ? (
        <button
          type="button"
          className="link sort"
          onClick={() => setSort({ by: column, up: sort.by === column ? !sort.up : true })}
        >
          {label}
          <span aria-hidden="true">{sort.by === column ? (sort.up ? " ▲" : " ▼") : ""}</span>
        </button>
      ) : (
        label
      )}
    </th>
  );

  return (
    <>
      {searchable && (
        <div className="field" style={{ maxWidth: 360 }}>
          <label htmlFor="home-search">{t("owner.search")}</label>
          <input id="home-search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      )}
      {/* On a phone the column headings are hidden, so sorting is a dropdown there. */}
      {searchable && (
        <div className="row phone-only" style={{ alignItems: "flex-end", marginBottom: "1rem" }}>
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="home-sort">{t("owner.sortBy")}</label>
            <select id="home-sort" value={sort.by} onChange={(e) => setSort({ by: e.target.value as Column, up: true })}>
              {(Object.keys(SORT) as Column[]).map((column) => (
                <option key={column} value={column}>{t(`owner.cols.${column}`)}</option>
              ))}
            </select>
          </div>
          <button type="button" onClick={() => setSort({ ...sort, up: !sort.up })}>
            {sort.up ? `▲ ${t("owner.ascending")}` : `▼ ${t("owner.descending")}`}
          </button>
        </div>
      )}
      <table className="responsive">
        <thead>
          <tr>
            {header("unit", t("owner.cols.unit"))}
            {header("resident", t("owner.cols.resident"))}
            {header("today", t("owner.cols.today"), true)}
            {header("month", t("owner.cols.month"), true)}
            {header("valve", t("owner.cols.valve"))}
            {header("ph", t("owner.cols.ph"))}
            {header("temp", t("owner.cols.temp"), true)}
            {header("online", t("owner.cols.online"))}
            {header("alerts", t("owner.cols.alerts"), true)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.home_id}>
              <td data-label={t("owner.cols.unit")}>
                <Link to={`/homes/${r.home_id}`}>{t("owner.unit", { unit: r.unit_no })}</Link>
              </td>
              <td data-label={t("owner.cols.resident")}>{r.residents.join(", ") || <span className="muted">{t("owner.empty")}</span>}</td>
              <td data-label={t("owner.cols.today")} className="num">{fmt.litres(r.litres_today)}</td>
              <td data-label={t("owner.cols.month")} className="num">{fmt.litres(r.litres_month)}</td>
              <td data-label={t("owner.cols.valve")}>
                {r.valve ? t(`valve.short.${r.valve}`, { defaultValue: r.valve }) : "-"}
                {r.locked && (
                  <span className="badge warn" title={r.lock_reason ?? ""} style={{ marginLeft: ".3rem" }}>
                    🔒 {t("owner.locked")}
                  </span>
                )}
              </td>
              <td data-label={t("owner.cols.ph")}>
                {r.ph === null ? "-" : (
                  <span className="row" style={{ gap: ".3rem", flexWrap: "nowrap" }}>
                    {fmt.number(r.ph)}
                    <QualityBadge label={fmt.phLabel(r.ph, overview.ph_safe_low, overview.ph_safe_high)} />
                  </span>
                )}
              </td>
              <td data-label={t("owner.cols.temp")} className="num">{r.temp_c === null ? "-" : `${fmt.number(r.temp_c)} °C`}</td>
              <td data-label={t("owner.cols.online")}>
                {r.device_id === null ? (
                  <span className="muted">{t("owner.noMeter")}</span>
                ) : r.online ? (
                  <span className="badge ok">● {t("owner.online")}</span>
                ) : (
                  <span className="badge bad" title={fmt.dateTime(r.last_seen_at)}>
                    ○ {t("owner.offlineSince", { when: fmt.ago(r.last_seen_at) })}
                  </span>
                )}
              </td>
              <td data-label={t("owner.cols.alerts")} className="num">
                {r.open_alerts > 0 ? <span className="badge warn">⚠ {r.open_alerts}</span> : "0"}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={9} className="muted">{t("owner.noMatch")}</td>
            </tr>
          )}
        </tbody>
      </table>
    </>
  );
}
