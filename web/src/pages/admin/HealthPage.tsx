// A-01: system health (FR-55, FR-31). Red or green for each service, devices
// online, readings per minute, refused device messages, emails that failed,
// and the last audit chain check. Asks again every 15 seconds.
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { api } from "../../api";
import { ErrorBox, Loading, Stat } from "../../components/ui";
import * as fmt from "../../format";
import type { Health, NotificationRow } from "../../types";

export function HealthPage() {
  const { t } = useTranslation();
  const health = useQuery({
    queryKey: ["admin-health"],
    queryFn: () => api<Health>("/admin/health"),
    refetchInterval: 15_000,
  });
  const failed = useQuery({
    queryKey: ["admin-failed-emails"],
    queryFn: () => api<NotificationRow[]>("/admin/notifications?status=failed&limit=20"),
    refetchInterval: 60_000,
  });

  if (health.isPending) return <Loading />;
  if (health.error) return <ErrorBox error={health.error} retry={() => health.refetch()} />;
  const h = health.data;
  const rejected = Object.entries(h.rejected_since_start);
  const check = h.last_audit_check;

  return (
    <>
      <h1>{t("admin.health.title")}</h1>
      <p className={`notice ${h.ok ? "ok" : "bad"}`} role="status">
        {h.ok ? `✓ ${t("admin.health.allOk")}` : `✕ ${t("admin.health.notOk")}`}
      </p>

      <section className="card" aria-labelledby="services-title" style={{ marginBottom: "1rem" }}>
        <h2 id="services-title">{t("admin.health.services")}</h2>
        <ul className="list">
          {h.services.map((s) => (
            <li key={s.name} className="row" style={{ justifyContent: "space-between" }}>
              <span>
                <strong>{t(`admin.health.service.${s.name}`)}</strong>
                <br />
                <span className="muted small">{s.detail}</span>
              </span>
              {/* Colour AND a symbol AND a word, never colour alone. */}
              <span className={`badge ${s.ok ? "ok" : "bad"}`}>
                <span aria-hidden="true">{s.ok ? "●" : "✕"}</span>
                {s.ok ? t("admin.health.up") : t("admin.health.down")}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <div className="grid" style={{ marginBottom: "1rem" }}>
        <Stat label={t("admin.health.devices")} value={`${h.devices_online} / ${h.devices_active}`}
              sub={t("admin.health.devicesSub")} />
        <Stat label={t("admin.health.readings")} value={fmt.number(h.readings_last_minute, 0)}
              sub={t("admin.health.readingsSub")} />
        <Stat label={t("admin.health.rejected")}
              value={fmt.number(rejected.reduce((sum, [, n]) => sum + n, 0), 0)}
              sub={h.received_since_start == null
                ? t("admin.health.noIngestor")
                : t("admin.health.ofReceived", { count: h.received_since_start })} />
        <Stat label={t("admin.health.emails")} value={h.notifications_failed_7d}
              sub={t("admin.health.emailsSub")} />
      </div>

      <div className="grid-2">
        <section className="card" aria-labelledby="rejected-title">
          <h2 id="rejected-title">{t("admin.health.rejectedWhy")}</h2>
          {rejected.length === 0 ? (
            <p className="muted">{t("admin.health.noneRejected")}</p>
          ) : (
            <table>
              <tbody>
                {rejected.map(([reason, count]) => (
                  <tr key={reason}>
                    <td><code>{reason}</code></td>
                    <td className="num">{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="card" aria-labelledby="audit-title">
          <h2 id="audit-title">{t("admin.health.audit")}</h2>
          {check ? (
            <p className={`notice ${check.ok ? "ok" : "bad"}`}>
              {check.ok
                ? t("admin.audit.chainOk", { count: check.entries, when: fmt.dateTime(check.at) })
                : t("admin.audit.chainBroken", { id: check.first_broken_id, when: fmt.dateTime(check.at) })}
            </p>
          ) : (
            <p className="muted">{t("admin.health.noCheck")}</p>
          )}
          <Link to="/audit">{t("admin.health.openAudit")}</Link>
        </section>

        <section className="card" aria-labelledby="backup-title">
          <h2 id="backup-title">{t("admin.health.backup")}</h2>
          <p className="muted">
            {h.last_backup_at ? fmt.dateTime(h.last_backup_at) : t("admin.health.noBackup")}
          </p>
          {h.last_backup_at &&
            Date.now() - new Date(h.last_backup_at).getTime() > 2 * 24 * 3600 * 1000 && (
              <p role="alert">{t("admin.health.oldBackup")}</p>
            )}
        </section>

        <section className="card" aria-labelledby="emails-title">
          <h2 id="emails-title">{t("admin.health.failedEmails")}</h2>
          {failed.isPending ? (
            <Loading />
          ) : failed.error ? (
            <ErrorBox error={failed.error} />
          ) : failed.data.length === 0 ? (
            <p className="muted">{t("admin.health.noFailed")}</p>
          ) : (
            <ul className="list">
              {failed.data.map((n) => (
                <li key={n.id}>
                  {fmt.dateTime(n.created_at)} · {n.kind} · {t("admin.health.tries", { count: n.attempts })}
                  {n.last_error && <div className="muted small">{n.last_error}</div>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}
