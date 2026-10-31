// O-08: alert rules (FR-62, FR-63). Switch each built-in rule on or off and
// change its numbers within the safe limits the API allows, and choose who
// is emailed for each severity. Everyone always sees alerts in the app.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { ErrorBox, Loading, SeverityBadge } from "../../components/ui";
import type { AlertRule, Severity } from "../../types";
import { NoBuilding, useBuilding } from "./building";

interface Rules {
  rules: AlertRule[];
  recipients: Record<Severity, string[]>;
}

const SEVERITIES: Severity[] = ["info", "warning", "critical"];

export function AlertRulesPage() {
  const { t } = useTranslation();
  const { buildingId, picker } = useBuilding();
  const rules = useQuery({
    queryKey: ["alert-rules", buildingId],
    queryFn: () => api<Rules>(`/buildings/${buildingId}/alert-rules`),
    enabled: buildingId !== null,
  });
  if (buildingId === null) return <NoBuilding />;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("alertRules.title")}</h1>
        {picker}
      </div>
      <p className="muted">{t("alertRules.intro")}</p>
      {rules.isPending ? (
        <Loading />
      ) : rules.error ? (
        <ErrorBox error={rules.error} retry={() => rules.refetch()} />
      ) : (
        // A new key when the saved rules change starts the form from them again.
        <RulesForm key={JSON.stringify(rules.data)} buildingId={buildingId} saved={rules.data} />
      )}
    </>
  );
}

function RulesForm({ buildingId, saved }: { buildingId: string; saved: Rules }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [rules, setRules] = useState(saved.rules);
  const [recipients, setRecipients] = useState(saved.recipients);
  const [done, setDone] = useState(false);
  const save = useMutation({
    mutationFn: (body: object) => api<Rules>(`/buildings/${buildingId}/alert-rules`, { method: "PATCH", body }),
    onSuccess: (fresh) => {
      setDone(true);
      queries.setQueryData(["alert-rules", buildingId], fresh);
    },
  });
  const change = (id: string, patch: Partial<AlertRule>) => {
    setDone(false);
    setRules(rules.map((r) => (r.rule === id ? { ...r, ...patch } : r)));
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    // Send only the rules that changed, so the audit log shows just those.
    const before = new Map(saved.rules.map((r) => [r.rule, JSON.stringify([r.enabled, r.values])]));
    const changed = rules.filter((r) => before.get(r.rule) !== JSON.stringify([r.enabled, r.values]));
    const body = Object.fromEntries(changed.map((r) => [r.rule, { enabled: r.enabled, ...r.values }]));
    save.mutate({ rules: body, recipients });
  };

  return (
    <form onSubmit={submit}>
      {rules.map((rule) => (
        <fieldset key={rule.rule} className="card" style={{ marginBottom: "1rem" }}>
          <legend className="sr-only">{rule.title}</legend>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <label className="check" style={{ fontWeight: 600 }}>
              <input type="checkbox" checked={rule.enabled} onChange={(e) => change(rule.rule, { enabled: e.target.checked })} />
              {rule.title}
            </label>
            <SeverityBadge severity={rule.severity} />
          </div>
          <p className="muted small">{rule.description}</p>
          {Object.keys(rule.limits).length > 0 && (
            <div className="grid">
              {Object.entries(rule.limits).map(([name, limit]) => {
                const id = `${rule.rule}-${name}`;
                const whole = [limit.min, limit.max, limit.default].every((n) => Number.isInteger(Number(n)));
                return (
                  <div className="field" key={name}>
                    <label htmlFor={id}>{String(limit.help)}</label>
                    <input id={id} type="number" required disabled={!rule.enabled}
                           min={limit.min} max={limit.max} step={whole ? 1 : 0.1}
                           value={rule.values[name] ?? ""} aria-describedby={`${id}-help`}
                           onChange={(e) => change(rule.rule, { values: { ...rule.values, [name]: Number(e.target.value) } })} />
                    <div className="help" id={`${id}-help`}>
                      {t("alertRules.range", { min: limit.min, max: limit.max, default: limit.default })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </fieldset>
      ))}

      <fieldset className="card" style={{ marginBottom: "1rem" }}>
        <legend><h2 style={{ margin: 0 }}>{t("alertRules.who")}</h2></legend>
        <p className="muted small">{t("alertRules.whoHelp")}</p>
        <table className="responsive">
          <thead>
            <tr>
              <th>{t("alertRules.severity")}</th>
              <th>{t("roles.owner")}</th>
              <th>{t("roles.resident")}</th>
            </tr>
          </thead>
          <tbody>
            {SEVERITIES.map((severity) => (
              <tr key={severity}>
                <td data-label={t("alertRules.severity")}><SeverityBadge severity={severity} /></td>
                {(["owner", "resident"] as const).map((who) => (
                  <td key={who} data-label={t(`roles.${who}`)}>
                    <input type="checkbox" aria-label={t("alertRules.emailTo", { who: t(`roles.${who}`), severity: t(`alerts.severities.${severity}`) })}
                           checked={recipients[severity]?.includes(who) ?? false}
                           onChange={(e) => {
                             setDone(false);
                             const now = new Set(recipients[severity] ?? []);
                             if (e.target.checked) now.add(who);
                             else now.delete(who);
                             setRecipients({ ...recipients, [severity]: [...now] });
                           }} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </fieldset>

      {save.error && <ErrorBox error={save.error} />}
      {done && <p className="notice ok" role="status">{t("alertRules.saved")}</p>}
      <button className="primary" type="submit" disabled={save.isPending}>{t("alertRules.save")}</button>
    </form>
  );
}
