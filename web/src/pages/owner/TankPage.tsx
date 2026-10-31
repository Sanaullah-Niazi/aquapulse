// O-04: the tank and pump (FR-47): a live gauge, pump state, auto/manual
// mode, thresholds, the level over 7 days and the pump's daily hours.
// Settings go to the tank device; the screen shows them once it confirms.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { BarsChart, LinesChart } from "../../components/Charts";
import { ErrorBox, Field, Loading, Stat } from "../../components/ui";
import * as fmt from "../../format";
import { useLiveEvents } from "../../live";
import type { Command, Tank, TankHistory } from "../../types";
import { NoBuilding, useBuilding } from "./building";
import { isoDaysAgo } from "./dates";
import { TankGauge } from "./OverviewPage";

export function TankPage() {
  const { t } = useTranslation();
  const { buildingId, picker } = useBuilding();
  const queries = useQueryClient();
  const tank = useQuery({
    queryKey: ["tank", buildingId],
    queryFn: () => api<Tank | null>(`/buildings/${buildingId}/tank`).catch((e) => (e.status === 404 ? null : Promise.reject(e))),
    enabled: buildingId !== null,
  });
  const tankId = tank.data?.id;
  const history = useQuery({
    queryKey: ["tank-history", tankId],
    queryFn: () => api<TankHistory>(`/tanks/${tankId}/history?from=${isoDaysAgo(6)}`),
    enabled: !!tankId,
    refetchInterval: 5 * 60_000,
  });
  useLiveEvents((event) => {
    if (event.buildingId !== buildingId) return;
    if (event.type === "tank") {
      // The gauge follows each tank reading at once.
      queries.setQueryData<Tank | null>(["tank", buildingId], (old) =>
        old && old.reading
          ? {
              ...old,
              online: true,
              mode: (event.mode as string) ?? old.mode,
              reading: {
                ...old.reading,
                time: event.time ?? old.reading.time,
                level_pct: (event.levelPct as number | null) ?? null,
                level_l: event.levelPct == null ? null : Math.round(((event.levelPct as number) / 100) * old.capacity_l),
                pump_on: Boolean(event.pumpOn),
                sump_ok: Boolean(event.sumpOk),
                dry_run_protect: Boolean(event.dryRunProtect),
                overflow_protect: Boolean(event.overflowProtect),
                mode: (event.mode as string) ?? old.reading.mode,
              },
            }
          : old,
      );
    } else if (event.type === "command" && event.homeId == null) {
      void queries.invalidateQueries({ queryKey: ["tank", buildingId] });
    }
  });

  if (buildingId === null) return <NoBuilding />;
  if (tank.isPending) return <Loading />;
  if (tank.error) return <ErrorBox error={tank.error} retry={() => tank.refetch()} />;
  if (tank.data === null) return <><h1>{t("tank.title")}</h1><p className="notice info">{t("tank.none")}</p></>;
  const k = tank.data;
  const r = k.reading;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("tank.title")}</h1>
        {picker}
      </div>
      {!k.online && <p className="notice warn">⚠ {t("tank.offlineSince", { when: fmt.ago(k.last_seen_at) })}</p>}
      {r && !r.sump_ok && <p className="notice bad">⛔ {t("tank.sumpEmpty")}</p>}
      {r?.overflow_protect && <p className="notice bad">⛔ {t("tank.overflow")}</p>}
      <div className="grid-2" style={{ marginBottom: "1rem" }}>
        <section className="card" aria-labelledby="gauge-title">
          <h2 id="gauge-title">{t("tank.level")}</h2>
          <TankGauge percent={r?.level_pct ?? null} size="large" />
          <p className="muted">
            {r?.level_l != null && t("tank.litres", { litres: fmt.litres(r.level_l), capacity: fmt.litres(k.capacity_l) })}
            <br />
            {t("tank.updated", { when: fmt.ago(r?.time) })}
          </p>
        </section>
        <PumpCard tank={k} />
      </div>
      <div className="grid-2" style={{ marginBottom: "1rem" }}>
        <SettingsCard tank={k} />
        <section className="card" aria-labelledby="hist-title">
          <h2 id="hist-title">{t("tank.week")}</h2>
          {history.isPending ? (
            <Loading />
          ) : history.error ? (
            <ErrorBox error={history.error} />
          ) : (
            <LinesChart
              title={t("tank.level")}
              unit="%"
              low={k.low_pct}
              high={k.high_pct}
              domain={[0, 100]}
              data={history.data.levels
                .filter((p) => p.level_pct !== null)
                .map((p) => ({ label: new Date(p.start).toLocaleDateString([], { weekday: "short", hour: "2-digit" }), value: p.level_pct! }))}
            />
          )}
        </section>
      </div>
      <section className="card" aria-labelledby="pumpday-title">
        <h2 id="pumpday-title">{t("tank.pumpHours")}</h2>
        {history.data && (
          <>
            <BarsChart title={t("tank.pumpHours")} unit="h" data={history.data.days.map((d) => ({ label: fmt.date(d.day), value: d.pump_hours }))} />
            <p className="muted small">
              {t("tank.energy", {
                kwh: fmt.number(history.data.days.reduce((s, d) => s + d.energy_kwh, 0)),
                kw: k.pump_kw,
              })}
            </p>
          </>
        )}
      </section>
    </>
  );
}

function useCommandWatch(buildingId: string | undefined) {
  const queries = useQueryClient();
  return (commands: Command[]) => {
    if (commands.length) void queries.invalidateQueries({ queryKey: ["tank", buildingId] });
  };
}

function PumpCard({ tank }: { tank: Tank }) {
  const { t } = useTranslation();
  const { buildingId } = useBuilding();
  const queries = useQueryClient();
  const [confirm, setConfirm] = useState<"on" | "off" | null>(null);
  const pump = useMutation({
    mutationFn: (action: "on" | "off") => api<Command>(`/tanks/${tank.id}/pump`, { method: "POST", body: { action, reason: "" } }),
    onSuccess: () => {
      setConfirm(null);
      void queries.invalidateQueries({ queryKey: ["tank", buildingId] });
    },
  });
  const on = tank.reading?.pump_on;
  const manual = tank.mode === "manual";
  const waiting = tank.open_commands.filter((c) => c.state === "queued" || c.state === "pending");
  return (
    <section className="card" aria-labelledby="pump-title">
      <h2 id="pump-title">{t("tank.pump")}</h2>
      <div className="grid">
        <Stat label={t("tank.pump")} value={on === undefined ? "-" : t(on ? "tank.pumpOn" : "tank.pumpOff")} />
        <Stat label={t("tank.mode")} value={t(`tank.modes.${tank.mode}`)} sub={t(manual ? "tank.manualHelp" : "tank.autoHelp", { low: tank.low_pct, high: tank.high_pct })} />
      </div>
      {manual && (
        confirm ? (
          <div className="notice info" role="alertdialog" aria-labelledby="pump-confirm">
            <p id="pump-confirm">{t(confirm === "on" ? "tank.confirmOn" : "tank.confirmOff")}</p>
            <div className="row">
              <button className="primary" autoFocus disabled={pump.isPending} onClick={() => pump.mutate(confirm)}>{t("valve.yes")}</button>
              <button onClick={() => setConfirm(null)}>{t("app.cancel")}</button>
            </div>
          </div>
        ) : (
          <button className={on ? "danger" : "primary"} disabled={!tank.online || waiting.length > 0} onClick={() => setConfirm(on ? "off" : "on")}>
            {t(on ? "tank.turnOff" : "tank.turnOn")}
          </button>
        )
      )}
      {waiting.length > 0 && <p className="muted" aria-live="polite">{t("valve.pending")}</p>}
      {pump.error && <ErrorBox error={pump.error} />}
    </section>
  );
}

/** Thresholds and auto/manual mode. Switching to manual needs a confirmation (FR-47). */
function SettingsCard({ tank }: { tank: Tank }) {
  const { t } = useTranslation();
  const { buildingId } = useBuilding();
  const watch = useCommandWatch(buildingId ?? undefined);
  const [form, setForm] = useState({ low: String(tank.low_pct), high: String(tank.high_pct), kw: String(tank.pump_kw) });
  const [manualAsk, setManualAsk] = useState(false);
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<{ commands: Command[] }>(`/tanks/${tank.id}/settings`, { method: "PATCH", body }),
    onSuccess: (r) => {
      setManualAsk(false);
      watch(r.commands);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate({ low_pct: Number(form.low), high_pct: Number(form.high), pump_kw: Number(form.kw) });
  };
  return (
    <section className="card" aria-labelledby="set-title">
      <h2 id="set-title">{t("tank.settings")}</h2>
      <form onSubmit={submit}>
        <div className="grid">
          <Field id="t-low" label={t("tank.low")}>
            <input id="t-low" type="number" min={5} max={87} step={1} required value={form.low} onChange={(e) => setForm({ ...form, low: e.target.value })} />
          </Field>
          <Field id="t-high" label={t("tank.high")}>
            <input id="t-high" type="number" min={15} max={97} step={1} required value={form.high} onChange={(e) => setForm({ ...form, high: e.target.value })} />
          </Field>
          <Field id="t-kw" label={t("tank.kw")}>
            <input id="t-kw" type="number" min={0.01} max={50} step={0.01} required value={form.kw} onChange={(e) => setForm({ ...form, kw: e.target.value })} />
          </Field>
        </div>
        <p className="muted small">{t("tank.settingsHelp")}</p>
        <button className="primary" disabled={save.isPending}>{t("app.save")}</button>
      </form>
      <h3 style={{ marginTop: "1.25rem" }}>{t("tank.mode")}</h3>
      {tank.mode === "auto" ? (
        manualAsk ? (
          <div className="notice warn" role="alertdialog" aria-labelledby="manual-confirm">
            <p id="manual-confirm">{t("tank.confirmManual")}</p>
            <div className="row">
              <button className="danger" autoFocus disabled={save.isPending} onClick={() => save.mutate({ mode: "manual" })}>{t("tank.toManual")}</button>
              <button onClick={() => setManualAsk(false)}>{t("app.cancel")}</button>
            </div>
          </div>
        ) : (
          <button onClick={() => setManualAsk(true)}>{t("tank.toManual")}</button>
        )
      ) : (
        <button className="primary" disabled={save.isPending} onClick={() => save.mutate({ mode: "auto" })}>{t("tank.toAuto")}</button>
      )}
      {save.isSuccess && save.data.commands.length > 0 && <p className="muted" aria-live="polite">{t("tank.sent")}</p>}
      {save.error && <ErrorBox error={save.error} />}
    </section>
  );
}
