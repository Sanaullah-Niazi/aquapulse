// O-03: one home (FR-43): live readings, charts, the water valve with the
// owner's lock (FR-44), who lives there (FR-46), its bills and its alerts.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { api, download } from "../../api";
import { AlertList } from "../../components/AlertList";
import { BarsChart } from "../../components/Charts";
import { BillStatusBadge, ErrorBox, Field, Loading, QualityBadge, Segmented, Stat } from "../../components/ui";
import { ValveControl } from "../../components/ValveControl";
import * as fmt from "../../format";
import { useHomeLive } from "../../hooks";
import type { Alert, BillSummary, Consumption, Home, HomeLive, Occupancy } from "../../types";
import { isoDaysAgo, today } from "./dates";

const RANGES = {
  "24h": { granularity: "hour", from: () => isoDaysAgo(1) },
  "30d": { granularity: "day", from: () => isoDaysAgo(29) },
  "12m": { granularity: "month", from: () => isoDaysAgo(364) },
} as const;
type Range = keyof typeof RANGES;

export function HomeDetailPage() {
  const { t } = useTranslation();
  const { homeId = "" } = useParams();
  const home = useQuery({ queryKey: ["home", homeId], queryFn: () => api<Home>(`/homes/${homeId}`) });
  const live = useHomeLive(homeId);
  if (home.isPending || live.isPending) return <Loading />;
  if (home.error || live.error) return <ErrorBox error={home.error ?? live.error} />;
  const l = live.data;
  const ph = l.reading?.ph;

  return (
    <>
      <p className="small" style={{ margin: 0 }}>
        <Link to="/homes">← {t("owner.homes")}</Link>
      </p>
      <h1>{t("owner.unit", { unit: home.data.unit_no })}</h1>
      <div className="grid" style={{ marginBottom: "1rem" }}>
        <Stat
          label={t("home.flow")}
          value={l.reading && l.reading.flow_lpm > 0 ? `${fmt.number(l.reading.flow_lpm)} L/min` : t("home.notRunning")}
          sub={l.device_id === null ? t("owner.noMeter") : l.online ? t("owner.online") : t("owner.offlineSince", { when: fmt.ago(l.last_seen_at) })}
        />
        <Stat
          label={t("home.quality")}
          value={ph === undefined ? "-" : `pH ${fmt.number(ph)}`}
          sub={<QualityBadge label={fmt.phLabel(ph, l.ph_safe_low, l.ph_safe_high)} />}
        />
        <Stat label={t("home.temperature")} value={l.reading ? `${fmt.number(l.reading.temp_c)} °C` : "-"} />
        <Stat label={t("owner.meter")} value={l.reading ? fmt.litres(l.reading.total_l) : "-"} sub={l.device_id ?? undefined} />
      </div>
      <div className="grid-2">
        <section className="card" aria-labelledby="valve-title">
          <h2 id="valve-title">{t("valve.title")}</h2>
          <ValveControl live={l} unit={home.data.unit_no} />
          <LockControl live={l} />
        </section>
        <UsageCard homeId={homeId} unit={home.data.unit_no} />
        <ResidentsCard homeId={homeId} />
        <HomeBillsCard home={home.data} />
        <HomeAlertsCard homeId={homeId} />
      </div>
    </>
  );
}

/** FR-27 / FR-44: lock the valve open or closed with a reason the residents see. */
function LockControl({ live }: { live: HomeLive }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<"open" | "closed">("closed");
  const [reason, setReason] = useState("");
  const change = useMutation({
    mutationFn: () =>
      live.lock
        ? api(`/homes/${live.home_id}/valve-unlock`, { method: "POST", body: { reason } })
        : api(`/homes/${live.home_id}/valve-lock`, { method: "POST", body: { state, reason } }),
    onSuccess: () => {
      setOpen(false);
      setReason("");
      void queries.invalidateQueries({ queryKey: ["live", live.home_id] });
      void queries.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    change.mutate();
  };
  if (!open)
    return (
      <p style={{ marginTop: "1rem" }}>
        <button onClick={() => setOpen(true)}>{live.lock ? `🔓 ${t("lock.unlock")}` : `🔒 ${t("lock.lock")}`}</button>
      </p>
    );
  return (
    <form onSubmit={submit} className="notice info" style={{ marginTop: "1rem" }}>
      {!live.lock && (
        <Field id="lock-state" label={t("lock.state")}>
          <select id="lock-state" value={state} onChange={(e) => setState(e.target.value as "open" | "closed")}>
            <option value="closed">{t("lock.closed")}</option>
            <option value="open">{t("lock.open")}</option>
          </select>
        </Field>
      )}
      <Field id="lock-reason" label={t("lock.reason")} help={t("lock.reasonHelp")}>
        <textarea id="lock-reason" required minLength={10} maxLength={300} rows={2} value={reason}
                  aria-describedby="lock-reason-help" onChange={(e) => setReason(e.target.value)} />
      </Field>
      {change.error && <ErrorBox error={change.error} />}
      <div className="row">
        <button className="primary" disabled={change.isPending || reason.trim().length < 10}>
          {live.lock ? t("lock.unlock") : t("lock.lock")}
        </button>
        <button type="button" onClick={() => setOpen(false)}>{t("app.cancel")}</button>
      </div>
    </form>
  );
}

function UsageCard({ homeId, unit }: { homeId: string; unit: string }) {
  const { t } = useTranslation();
  const [range, setRange] = useState<Range>("30d");
  const { granularity, from } = RANGES[range];
  const usage = useQuery({
    queryKey: ["usage", homeId, granularity, range],
    queryFn: () => api<Consumption>(`/homes/${homeId}/consumption?granularity=${granularity}&from=${from()}`),
  });
  const csv = useMutation({
    mutationFn: () => download(`/homes/${homeId}/export/consumption.csv?from=${isoDaysAgo(364)}&to=${today()}`, `unit-${unit}.csv`),
  });
  const label = (iso: string) =>
    granularity === "hour"
      ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : granularity === "day"
        ? fmt.date(iso)
        : fmt.month(iso);
  return (
    <section className="card" aria-labelledby="usage-title">
      <h2 id="usage-title">{t("owner.usage")}</h2>
      <Segmented
        label={t("usage.range")}
        value={range}
        options={(Object.keys(RANGES) as Range[]).map((r) => ({ value: r, label: t(`owner.ranges.${r}`) }))}
        onChange={setRange}
      />
      {usage.isPending ? (
        <Loading />
      ) : usage.error ? (
        <ErrorBox error={usage.error} />
      ) : (
        <>
          <BarsChart title={t("owner.usage")} unit="L" data={usage.data.points.map((p) => ({ label: label(p.start), value: p.litres }))} />
          <p className="muted small">{t("usage.total", { litres: fmt.litres(usage.data.total_litres) })}</p>
        </>
      )}
      <button onClick={() => csv.mutate()} disabled={csv.isPending}>{t("owner.csv")}</button>
      {csv.error && <ErrorBox error={csv.error} />}
    </section>
  );
}

/** FR-46: who lives here, moving out, the billing contact, and inviting someone new. */
function ResidentsCard({ homeId }: { homeId: string }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const key = ["occupancies", homeId];
  const people = useQuery({ queryKey: key, queryFn: () => api<Occupancy[]>(`/homes/${homeId}/occupancies`) });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api(`/occupancies/${id}`, { method: "PATCH", body }),
    onSuccess: () => {
      void queries.invalidateQueries({ queryKey: key });
      void queries.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const [moveOut, setMoveOut] = useState<Record<string, string>>({});
  const current = (o: Occupancy) => o.move_out === null || o.move_out >= today();

  return (
    <section className="card" aria-labelledby="people-title">
      <h2 id="people-title">{t("people.title")}</h2>
      {people.isPending ? (
        <Loading />
      ) : people.error ? (
        <ErrorBox error={people.error} />
      ) : people.data.length === 0 ? (
        <p className="muted">{t("people.none")}</p>
      ) : (
        <ul className="list">
          {people.data.map((o) => (
            <li key={o.id}>
              <strong>{o.full_name}</strong> <span className="muted small">{o.email}</span>
              {o.is_billing_contact && <span className="badge info" style={{ marginLeft: ".4rem" }}>{t("people.billingContact")}</span>}
              <div className="muted small">
                {t("people.since", { date: fmt.date(o.move_in) })}
                {o.move_out && ` · ${t("people.until", { date: fmt.date(o.move_out) })}`}
              </div>
              {current(o) && (
                <div className="row" style={{ marginTop: ".4rem" }}>
                  <label className="sr-only" htmlFor={`out-${o.id}`}>{t("people.moveOutDate")}</label>
                  <input id={`out-${o.id}`} type="date" style={{ width: "auto" }} min={o.move_in}
                         value={moveOut[o.id] ?? o.move_out ?? ""} onChange={(e) => setMoveOut({ ...moveOut, [o.id]: e.target.value })} />
                  <button disabled={!moveOut[o.id] || update.isPending}
                          onClick={() => update.mutate({ id: o.id, body: { move_out: moveOut[o.id] } })}>
                    {t("people.setMoveOut")}
                  </button>
                  {!o.is_billing_contact && (
                    <button disabled={update.isPending} onClick={() => update.mutate({ id: o.id, body: { is_billing_contact: true } })}>
                      {t("people.makeBillingContact")}
                    </button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {update.error && <ErrorBox error={update.error} />}
      <InviteForm homeId={homeId} hasContact={people.data?.some((o) => o.is_billing_contact && current(o)) ?? false} />
    </section>
  );
}

function InviteForm({ homeId, hasContact }: { homeId: string; hasContact: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", full_name: "", move_in: today(), is_billing_contact: false });
  const invite = useMutation({
    mutationFn: () => api<{ email_sent: boolean }>(`/homes/${homeId}/invitations`, { method: "POST", body: form }),
    onSuccess: () => setForm({ email: "", full_name: "", move_in: today(), is_billing_contact: false }),
  });
  if (!open)
    return (
      <p>
        <button onClick={() => setOpen(true)}>{t("people.invite")}</button>
        {invite.isSuccess && <span className="notice ok" role="status" style={{ marginLeft: ".5rem" }}>{t("people.invited")}</span>}
      </p>
    );
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        invite.mutate(undefined, { onSuccess: () => setOpen(false) });
      }}
      style={{ marginTop: "1rem" }}
    >
      <h3>{t("people.invite")}</h3>
      <p className="muted small">{t("people.inviteHelp")}</p>
      <Field id="inv-name" label={t("people.name")}>
        <input id="inv-name" required maxLength={120} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
      </Field>
      <Field id="inv-email" label={t("login.email")}>
        <input id="inv-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
      </Field>
      <Field id="inv-in" label={t("people.moveIn")}>
        <input id="inv-in" type="date" required value={form.move_in} onChange={(e) => setForm({ ...form, move_in: e.target.value })} />
      </Field>
      <label className="check">
        <input type="checkbox" checked={form.is_billing_contact} disabled={hasContact}
               onChange={(e) => setForm({ ...form, is_billing_contact: e.target.checked })} />
        {t("people.isBillingContact")}
      </label>
      {hasContact && <p className="muted small">{t("people.hasContact")}</p>}
      {invite.error && <ErrorBox error={invite.error} />}
      <div className="row" style={{ marginTop: "1rem" }}>
        <button className="primary" disabled={invite.isPending}>{t("people.send")}</button>
        <button type="button" onClick={() => setOpen(false)}>{t("app.cancel")}</button>
      </div>
    </form>
  );
}

function HomeBillsCard({ home }: { home: Home }) {
  const { t } = useTranslation();
  const bills = useQuery({
    queryKey: ["bills", "building", home.building_id],
    queryFn: () => api<BillSummary[]>(`/buildings/${home.building_id}/bills`),
  });
  const mine = bills.data?.filter((b) => b.home_id === home.id).slice(0, 6) ?? [];
  return (
    <section className="card" aria-labelledby="hb-title">
      <h2 id="hb-title">{t("owner.billsOfHome")}</h2>
      {bills.isPending ? (
        <Loading />
      ) : bills.error ? (
        <ErrorBox error={bills.error} />
      ) : mine.length === 0 ? (
        <p className="muted">{t("owner.noBills")}</p>
      ) : (
        <ul className="list">
          {mine.map((b) => (
            <li key={b.id} className="row" style={{ justifyContent: "space-between" }}>
              <Link to={`/bills/${b.id}`}>{fmt.month(b.month)} · {b.bill_to}</Link>
              <span className="row">{b.total_text} <BillStatusBadge status={b.status} /></span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function HomeAlertsCard({ homeId }: { homeId: string }) {
  const { t } = useTranslation();
  const alerts = useQuery({
    queryKey: ["alerts", "home", homeId, "owner"],
    queryFn: () => api<Alert[]>(`/alerts?home_id=${homeId}&limit=5`),
  });
  return (
    <section className="card" aria-labelledby="ha-title">
      <h2 id="ha-title">{t("nav.alerts")}</h2>
      {alerts.isPending ? <Loading /> : alerts.error ? <ErrorBox error={alerts.error} /> : <AlertList alerts={alerts.data} compact />}
    </section>
  );
}
