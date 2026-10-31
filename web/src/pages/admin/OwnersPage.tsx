// A-02: owners and buildings (FR-55, FR-20). Add an owner, switch an owner off
// (their sessions end at once, FR-55 criterion 2) or on again, add a building
// for an owner, and move a building to another owner. All of it is audited.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { ErrorBox, Field, Loading } from "../../components/ui";
import { PasswordInput } from "../../components/PasswordInput";
import type { Building, UserRow } from "../../types";

// The 11 currencies AquaPulse knows (KWD, BHD and OMR have 3 decimals).
const CURRENCIES = ["PKR", "USD", "GBP", "EUR", "AUD", "AED", "SAR", "QAR", "KWD", "BHD", "OMR"];

export function OwnersPage() {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const owners = useQuery({ queryKey: ["admin-owners"], queryFn: () => api<UserRow[]>("/admin/owners") });
  const buildings = useQuery({ queryKey: ["buildings"], queryFn: () => api<Building[]>("/buildings") });
  const [stopping, setStopping] = useState<string | null>(null);
  const refresh = () => {
    setStopping(null);
    void queries.invalidateQueries({ queryKey: ["admin-owners"] });
    void queries.invalidateQueries({ queryKey: ["buildings"] });
  };
  const patchOwner = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api(`/admin/owners/${id}`, { method: "PATCH", body: { is_active } }),
    onSuccess: refresh,
  });
  const moveBuilding = useMutation({
    mutationFn: ({ id, owner_id }: { id: string; owner_id: string }) =>
      api(`/buildings/${id}`, { method: "PATCH", body: { owner_id } }),
    onSuccess: refresh,
  });

  if (owners.isPending || buildings.isPending) return <Loading />;
  if (owners.error || buildings.error) return <ErrorBox error={owners.error ?? buildings.error} />;
  const count = (id: string) => buildings.data.filter((b) => b.owner_id === id).length;
  const error = patchOwner.error ?? moveBuilding.error;

  return (
    <>
      <h1>{t("admin.owners.title")}</h1>
      {error && <ErrorBox error={error} />}
      <section className="card" aria-labelledby="owners-title" style={{ marginBottom: "1rem" }}>
        <h2 id="owners-title">{t("admin.owners.owners")}</h2>
        <table className="responsive">
          <thead>
            <tr>
              <th>{t("admin.owners.name")}</th>
              <th>{t("admin.owners.email")}</th>
              <th className="num">{t("admin.owners.buildings")}</th>
              <th>{t("admin.owners.status")}</th>
              <th><span className="sr-only">{t("admin.owners.actions")}</span></th>
            </tr>
          </thead>
          <tbody>
            {owners.data.map((o) => (
              <tr key={o.id}>
                <td data-label={t("admin.owners.name")}>{o.full_name}</td>
                <td data-label={t("admin.owners.email")} style={{ overflowWrap: "anywhere" }}>{o.email}</td>
                <td data-label={t("admin.owners.buildings")} className="num">{count(o.id)}</td>
                <td data-label={t("admin.owners.status")}>
                  <span className={`badge ${o.is_active ? "ok" : "bad"}`}>
                    {o.is_active ? t("admin.owners.active") : t("admin.owners.disabled")}
                  </span>
                </td>
                <td>
                  {!o.is_active ? (
                    <button disabled={patchOwner.isPending}
                            onClick={() => patchOwner.mutate({ id: o.id, is_active: true })}>
                      {t("admin.owners.enable")}
                    </button>
                  ) : stopping === o.id ? (
                    <span className="row">
                      <button className="danger" disabled={patchOwner.isPending}
                              onClick={() => patchOwner.mutate({ id: o.id, is_active: false })}>
                        {t("admin.owners.confirmDisable")}
                      </button>
                      <button onClick={() => setStopping(null)}>{t("app.cancel")}</button>
                    </span>
                  ) : (
                    <button onClick={() => setStopping(o.id)}
                            aria-label={t("admin.owners.disableWho", { name: o.full_name })}>
                      {t("admin.owners.disable")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {stopping && <p className="muted small" role="status">{t("admin.owners.disableHelp")}</p>}
      </section>

      <section className="card" aria-labelledby="buildings-title" style={{ marginBottom: "1rem" }}>
        <h2 id="buildings-title">{t("admin.owners.buildingsTitle")}</h2>
        <table className="responsive">
          <thead>
            <tr>
              <th>{t("admin.owners.building")}</th>
              <th>{t("admin.owners.where")}</th>
              <th>{t("admin.owners.owner")}</th>
            </tr>
          </thead>
          <tbody>
            {buildings.data.map((b) => (
              <tr key={b.id}>
                <td data-label={t("admin.owners.building")}>
                  {b.name} <span className="muted small">({b.code}, {b.currency_code})</span>
                </td>
                <td data-label={t("admin.owners.where")}>{b.timezone}</td>
                <td data-label={t("admin.owners.owner")}>
                  <select aria-label={t("admin.owners.ownerOf", { name: b.name })} value={b.owner_id ?? ""}
                          disabled={moveBuilding.isPending}
                          onChange={(e) => moveBuilding.mutate({ id: b.id, owner_id: e.target.value })}>
                    {owners.data.map((o) => (
                      <option key={o.id} value={o.id}>{o.full_name} ({o.email})</option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="grid-2">
        <NewOwner onDone={refresh} />
        <NewBuilding owners={owners.data.filter((o) => o.is_active)} onDone={refresh} />
      </div>
    </>
  );
}

function NewOwner({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const empty = { email: "", full_name: "", password: "" };
  const [form, setForm] = useState(empty);
  const [made, setMade] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api<UserRow>("/admin/owners", { method: "POST", body: form }),
    onSuccess: (owner) => {
      setMade(owner.email);
      setForm(empty);
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setMade(null);
    create.mutate();
  };
  return (
    <form className="card" onSubmit={submit} aria-labelledby="new-owner-title">
      <h2 id="new-owner-title">{t("admin.owners.newOwner")}</h2>
      <Field id="no-name" label={t("admin.owners.name")}>
        <input id="no-name" required maxLength={120} value={form.full_name}
               onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
      </Field>
      <Field id="no-email" label={t("admin.owners.email")}>
        <input id="no-email" type="email" required autoComplete="off" value={form.email}
               onChange={(e) => setForm({ ...form, email: e.target.value })} />
      </Field>
      <PasswordInput id="no-password" label={t("admin.owners.firstPassword")} value={form.password}
                     onChange={(value) => setForm({ ...form, password: value })} />
      <p className="muted small">{t("admin.owners.passwordHelp")}</p>
      {create.error && <ErrorBox error={create.error} />}
      {made && <p className="notice ok" role="status">{t("admin.owners.made", { email: made })}</p>}
      <button className="primary" disabled={create.isPending}>{t("admin.owners.create")}</button>
    </form>
  );
}

function NewBuilding({ owners, onDone }: { owners: UserRow[]; onDone: () => void }) {
  const { t } = useTranslation();
  const empty = { name: "", address: "", timezone: "Asia/Karachi", currency_code: "PKR", owner_id: owners[0]?.id ?? "" };
  const [form, setForm] = useState(empty);
  const [made, setMade] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api<Building>("/buildings", { method: "POST", body: form }),
    onSuccess: (building) => {
      setMade(building.name);
      setForm({ ...empty, owner_id: form.owner_id });
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setMade(null);
    create.mutate();
  };
  return (
    <form className="card" onSubmit={submit} aria-labelledby="new-building-title">
      <h2 id="new-building-title">{t("admin.owners.newBuilding")}</h2>
      <Field id="nb-name" label={t("settings.name")}>
        <input id="nb-name" required maxLength={120} value={form.name}
               onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </Field>
      <Field id="nb-owner" label={t("admin.owners.owner")}>
        <select id="nb-owner" required value={form.owner_id} onChange={(e) => setForm({ ...form, owner_id: e.target.value })}>
          {owners.map((o) => <option key={o.id} value={o.id}>{o.full_name}</option>)}
        </select>
      </Field>
      <Field id="nb-tz" label={t("settings.timezone")} help={t("settings.timezoneHelp")}>
        <input id="nb-tz" required value={form.timezone} aria-describedby="nb-tz-help"
               onChange={(e) => setForm({ ...form, timezone: e.target.value })} />
      </Field>
      <Field id="nb-currency" label={t("settings.currency")} help={t("settings.currencyHelp")}>
        <select id="nb-currency" value={form.currency_code} aria-describedby="nb-currency-help"
                onChange={(e) => setForm({ ...form, currency_code: e.target.value })}>
          {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </Field>
      {create.error && <ErrorBox error={create.error} />}
      {made && <p className="notice ok" role="status">{t("admin.owners.buildingMade", { name: made })}</p>}
      <button className="primary" disabled={create.isPending || owners.length === 0}>{t("admin.owners.createBuilding")}</button>
    </form>
  );
}
