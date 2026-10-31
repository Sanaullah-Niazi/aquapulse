// S-08: the alerts inbox. New alerts appear by themselves (live).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { AlertList } from "../../components/AlertList";
import { ErrorBox, Loading, Segmented } from "../../components/ui";
import { useLiveEvents } from "../../live";
import type { Alert, Severity } from "../../types";

export function AlertsPage() {
  const { t } = useTranslation();
  const [active, setActive] = useState(true);
  const [severity, setSeverity] = useState<Severity | "">("");
  const queries = useQueryClient();
  const params = new URLSearchParams({ limit: "100" });
  if (active) params.set("active", "true");
  if (severity) params.set("severity", severity);
  const alerts = useQuery({
    queryKey: ["alerts", active, severity],
    queryFn: () => api<Alert[]>(`/alerts?${params}`),
  });
  useLiveEvents((event) => {
    if (event.type === "alert") void queries.invalidateQueries({ queryKey: ["alerts"] });
  });

  return (
    <>
      <h1>{t("alerts.title")}</h1>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <Segmented
          label={t("alerts.title")}
          value={active ? "active" : "all"}
          options={[
            { value: "active", label: t("alerts.active") },
            { value: "all", label: t("alerts.all") },
          ]}
          onChange={(value) => setActive(value === "active")}
        />
        <label className="sr-only" htmlFor="severity">
          {t("alerts.severity")}
        </label>
        <select
          id="severity"
          style={{ width: "auto" }}
          value={severity}
          onChange={(e) => setSeverity(e.target.value as Severity | "")}
        >
          <option value="">
            {t("alerts.severity")}: {t("alerts.anySeverity")}
          </option>
          {(["critical", "warning", "info"] as const).map((level) => (
            <option key={level} value={level}>
              {t(`alerts.severities.${level}`)}
            </option>
          ))}
        </select>
      </div>
      <div className="card">
        {alerts.isPending ? (
          <Loading />
        ) : alerts.error ? (
          <ErrorBox error={alerts.error} retry={() => alerts.refetch()} />
        ) : (
          <AlertList alerts={alerts.data} />
        )}
      </div>
    </>
  );
}
