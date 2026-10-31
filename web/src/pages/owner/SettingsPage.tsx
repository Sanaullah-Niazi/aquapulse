// O-09: building settings (FR-20, FR-27, FR-35). Name and address, time zone,
// currency (fixed once the building has bills), days to pay, whether monthly
// bills are issued at once, and whether water may be shut off for an overdue bill.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { ErrorBox, Field, Loading } from "../../components/ui";
import type { Building } from "../../types";
import { NoBuilding, useBuilding } from "./building";

// The 11 currencies AquaPulse knows (KWD, BHD and OMR have 3 decimals).
const CURRENCIES = ["PKR", "USD", "GBP", "EUR", "AUD", "AED", "SAR", "QAR", "KWD", "BHD", "OMR"];

function timeZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return []; // an older browser: the field still accepts typing
  }
}

export function SettingsPage() {
  const { t } = useTranslation();
  const { buildingId, building, picker } = useBuilding();
  if (buildingId === null) return <NoBuilding />;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("settings.title")}</h1>
        {picker}
      </div>
      {/* A new key when the saved building changes starts the form from it again. */}
      {building ? <SettingsForm key={JSON.stringify(building)} building={building} /> : <Loading />}
    </>
  );
}

function SettingsForm({ building }: { building: Building }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [form, setForm] = useState(building);
  const [done, setDone] = useState(false);
  const save = useMutation({
    mutationFn: (body: Partial<Building>) => api<Building>(`/buildings/${building.id}`, { method: "PATCH", body }),
    onSuccess: () => {
      setDone(true);
      void queries.invalidateQueries({ queryKey: ["buildings"] });
      void queries.invalidateQueries({ queryKey: ["overview", building.id] });
    },
  });
  const set = (patch: Partial<Building>) => {
    setDone(false);
    setForm({ ...form, ...patch });
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    // Send only what changed, so an unchanged currency is never a conflict.
    const keys = ["name", "address", "timezone", "currency_code", "due_days", "bills_auto_issue",
                  "nonpayment_shutoff_enabled"] as const;
    const body = Object.fromEntries(keys.filter((k) => form[k] !== building[k]).map((k) => [k, form[k]]));
    save.mutate(body);
  };

  return (
    <form className="card" onSubmit={submit} style={{ maxWidth: 640 }}>
      <Field id="b-name" label={t("settings.name")}>
        <input id="b-name" required maxLength={120} value={form.name} onChange={(e) => set({ name: e.target.value })} />
      </Field>
      <Field id="b-address" label={t("settings.address")}>
        <textarea id="b-address" rows={2} maxLength={300} value={form.address}
                  onChange={(e) => set({ address: e.target.value })} />
      </Field>
      <Field id="b-tz" label={t("settings.timezone")} help={t("settings.timezoneHelp")}>
        <input id="b-tz" required list="time-zones" value={form.timezone} aria-describedby="b-tz-help"
               onChange={(e) => set({ timezone: e.target.value })} />
        <datalist id="time-zones">
          {timeZones().map((zone) => <option key={zone} value={zone} />)}
        </datalist>
      </Field>
      <Field id="b-currency" label={t("settings.currency")} help={t("settings.currencyHelp")}>
        <select id="b-currency" value={form.currency_code} aria-describedby="b-currency-help"
                onChange={(e) => set({ currency_code: e.target.value })}>
          {CURRENCIES.map((code) => <option key={code} value={code}>{code}</option>)}
        </select>
      </Field>
      <Field id="b-due" label={t("settings.dueDays")} help={t("settings.dueDaysHelp")}>
        <input id="b-due" type="number" min={1} max={90} step={1} required value={form.due_days}
               aria-describedby="b-due-help" style={{ maxWidth: "8rem" }}
               onChange={(e) => set({ due_days: Number(e.target.value) })} />
      </Field>
      <div className="field">
        <label className="check">
          <input type="checkbox" checked={form.bills_auto_issue}
                 onChange={(e) => set({ bills_auto_issue: e.target.checked })} />
          {t("settings.autoIssue")}
        </label>
        <div className="help">{t("settings.autoIssueHelp")}</div>
      </div>
      <div className="field">
        <label className="check">
          <input type="checkbox" checked={form.nonpayment_shutoff_enabled}
                 onChange={(e) => set({ nonpayment_shutoff_enabled: e.target.checked })} />
          {t("settings.shutoff")}
        </label>
        <div className="help">{t("settings.shutoffHelp")}</div>
      </div>
      <p className="muted small">{t("settings.code", { code: building.code })}</p>
      {save.error && <ErrorBox error={save.error} />}
      {done && <p className="notice ok" role="status">{t("settings.saved")}</p>}
      <button className="primary" type="submit" disabled={save.isPending}>{t("settings.save")}</button>
    </form>
  );
}
