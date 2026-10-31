// S-07: which alerts also come by email, never-by-email types, and quiet hours (FR-52).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { useMe } from "../../auth";
import { ErrorBox, Field, Loading } from "../../components/ui";
import type { NotificationPrefs } from "../../types";

const HOME_RULES = ["ph_unsafe", "temp_abnormal", "device_offline", "high_usage", "command_failed",
                    "sensor_stuck", "leak", "burst", "unusual_use"];
const BUILDING_RULES = ["tank_low", "tank_overflow", "dry_run"];

export function NotificationsPage() {
  const { t } = useTranslation();
  const me = useMe();
  const queries = useQueryClient();
  const prefs = useQuery({
    queryKey: ["prefs"],
    queryFn: () => api<NotificationPrefs>("/me/notifications"),
  });
  const [form, setForm] = useState<NotificationPrefs | null>(null);
  useEffect(() => {
    if (prefs.data) setForm(prefs.data);
  }, [prefs.data]);
  const save = useMutation({
    // Only the choices go back: push_available is the server's answer, not a choice.
    mutationFn: ({ push_available: _, ...body }: NotificationPrefs) =>
      api<NotificationPrefs>("/me/notifications", { method: "PATCH", body }),
    onSuccess: (data) => queries.setQueryData(["prefs"], data),
  });

  if (prefs.isPending || !form) return prefs.error ? <ErrorBox error={prefs.error} /> : <Loading />;

  // Residents only get home alerts; owners also tank alerts; admins the audit alert.
  const rules = [
    ...HOME_RULES,
    ...(me.role !== "resident" ? BUILDING_RULES : []),
    ...(me.role === "sysadmin" ? ["audit_chain"] : []),
  ];
  const quiet = form.quiet_from !== null;
  const toggleRule = (rule: string) =>
    setForm({
      ...form,
      email_muted_rules: form.email_muted_rules.includes(rule)
        ? form.email_muted_rules.filter((r) => r !== rule)
        : [...form.email_muted_rules, rule],
    });

  return (
    <>
      <h1>{t("notifications.title")}</h1>
      <form
        className="card"
        style={{ maxWidth: 640 }}
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(form);
        }}
      >
        <p className="muted">{t("notifications.help")}</p>
        <Field id="level" label={t("notifications.emailMin")}>
          <select
            id="level"
            value={form.alert_email_min}
            onChange={(e) =>
              setForm({ ...form, alert_email_min: e.target.value as NotificationPrefs["alert_email_min"] })
            }
          >
            {(["info", "warning", "critical", "off"] as const).map((level) => (
              <option key={level} value={level}>
                {t(`notifications.levels.${level}`)}
              </option>
            ))}
          </select>
        </Field>

        <fieldset className="field" style={{ border: 0, padding: 0 }}>
          <legend style={{ fontWeight: 600, marginBottom: ".25rem" }}>{t("notifications.types")}</legend>
          {rules.map((rule) => (
            <label key={rule} className="check">
              <input
                type="checkbox"
                checked={form.email_muted_rules.includes(rule)}
                onChange={() => toggleRule(rule)}
              />
              {t(`rules.${rule}`)}
            </label>
          ))}
        </fieldset>

        <fieldset className="field" style={{ border: 0, padding: 0 }}>
          <legend style={{ fontWeight: 600, marginBottom: ".25rem" }}>{t("notifications.quiet")}</legend>
          <p className="help muted small">{t("notifications.quietHelp")}</p>
          <label className="check">
            <input
              type="checkbox"
              checked={quiet}
              onChange={() =>
                setForm(
                  quiet
                    ? { ...form, quiet_from: null, quiet_until: null }
                    : { ...form, quiet_from: "22:00", quiet_until: "07:00" },
                )
              }
            />
            {t("notifications.quietOn")}
          </label>
          {quiet && (
            <div className="row" style={{ marginTop: ".5rem" }}>
              <Field id="from" label={t("notifications.from")}>
                <input id="from" type="time" required value={form.quiet_from?.slice(0, 5) ?? ""}
                       onChange={(e) => setForm({ ...form, quiet_from: e.target.value })} />
              </Field>
              <Field id="until" label={t("notifications.until")}>
                <input id="until" type="time" required value={form.quiet_until?.slice(0, 5) ?? ""}
                       onChange={(e) => setForm({ ...form, quiet_until: e.target.value })} />
              </Field>
            </div>
          )}
        </fieldset>
        <p className="muted small">{t("notifications.push")}</p>
        {save.error && <ErrorBox error={save.error} />}
        {save.isSuccess && <p className="notice ok" role="status">{t("app.saved")}</p>}
        <button className="primary" disabled={save.isPending}>
          {t("app.save")}
        </button>
      </form>
    </>
  );
}
