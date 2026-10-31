// A list of alerts with "I have seen it" (and "Mark as fixed" for owners and
// admins). Used on the Alerts page and, shortened, on My home.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { useMe } from "../auth";
import * as fmt from "../format";
import type { Alert } from "../types";
import { ErrorBox, SeverityBadge } from "./ui";

export function AlertList({ alerts, compact = false }: { alerts: Alert[]; compact?: boolean }) {
  const { t } = useTranslation();
  if (alerts.length === 0) return <p className="muted">{t("alerts.none")}</p>;
  return (
    <ul className="list">
      {alerts.map((alert) => (
        <AlertItem key={alert.id} alert={alert} compact={compact} />
      ))}
    </ul>
  );
}

function AlertItem({ alert, compact }: { alert: Alert; compact: boolean }) {
  const { t } = useTranslation();
  const me = useMe();
  const queries = useQueryClient();
  const [note, setNote] = useState("");
  const [resolving, setResolving] = useState(false);
  const action = useMutation({
    mutationFn: (kind: "acknowledge" | "resolve") =>
      api(`/alerts/${alert.id}/${kind}`, {
        method: "POST",
        body: kind === "resolve" ? { note } : undefined,
      }),
    onSuccess: () => queries.invalidateQueries({ queryKey: ["alerts"] }),
  });
  const closed = alert.state === "resolved" || alert.state === "auto_resolved";
  // Owners close building alerts; System Admins close system alerts (FR-19, FR-59).
  const canResolve =
    !closed &&
    ((me.role === "owner" && alert.building_id !== null) ||
      (me.role === "sysadmin" && alert.building_id === null));

  return (
    <li>
      <div className="row">
        <SeverityBadge severity={alert.severity} />
        <strong>{alert.title}</strong>
        <span className="badge">{t(`alerts.states.${alert.state}`)}</span>
      </div>
      <p style={{ margin: ".35rem 0" }}>{alert.message}</p>
      <div className="small muted">
        {t("alerts.opened", { when: fmt.dateTime(alert.opened_at) })}
        {!compact && alert.seen_count > 1 && <> · {t("alerts.seen", { count: alert.seen_count })}</>}
        {!closed && alert.cleared_at && <> · {t("alerts.cleared", { when: fmt.ago(alert.cleared_at) })}</>}
      </div>
      {!compact && !closed && (
        <div className="row" style={{ marginTop: ".5rem" }}>
          {alert.state === "open" && (me.role !== "sysadmin" || alert.building_id === null) && (
            <button disabled={action.isPending} onClick={() => action.mutate("acknowledge")}>
              {t("alerts.acknowledge")}
            </button>
          )}
          {canResolve && !resolving && (
            <button onClick={() => setResolving(true)}>{t("alerts.resolve")}</button>
          )}
        </div>
      )}
      {resolving && (
        <form
          className="row"
          style={{ marginTop: ".5rem" }}
          onSubmit={(e) => {
            e.preventDefault();
            action.mutate("resolve");
          }}
        >
          <label className="sr-only" htmlFor={`note-${alert.id}`}>
            {t("alerts.resolveNote")}
          </label>
          <input
            id={`note-${alert.id}`}
            placeholder={t("alerts.resolveNote")}
            value={note}
            maxLength={300}
            onChange={(e) => setNote(e.target.value)}
            style={{ flex: "1 1 200px", width: "auto" }}
          />
          <button className="primary" disabled={action.isPending}>
            {t("alerts.resolve")}
          </button>
          <button type="button" onClick={() => setResolving(false)}>
            {t("app.cancel")}
          </button>
        </form>
      )}
      {action.error && <ErrorBox error={action.error} />}
    </li>
  );
}
