// O-05: the building's water meters and tank device (FR-46): online or not,
// last seen, firmware; register a new one, give it a new password, or revoke it.
// A new password is shown ONCE: it is not stored readable, so a reload cannot show it again.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { ErrorBox, Loading } from "../../components/ui";
import * as fmt from "../../format";
import type { Device, DeviceCredentials } from "../../types";
import { NoBuilding, useBuilding, useOverview } from "./building";

export function DevicesPage() {
  const { t } = useTranslation();
  const { buildingId, picker } = useBuilding();
  const overview = useOverview(buildingId);
  const queries = useQueryClient();
  const devices = useQuery({
    queryKey: ["devices", buildingId],
    queryFn: () => api<Device[]>(`/buildings/${buildingId}/devices`),
    enabled: buildingId !== null,
    refetchInterval: 30_000,
  });
  const [secret, setSecret] = useState<DeviceCredentials | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const done = (creds?: DeviceCredentials) => {
    if (creds) setSecret(creds);
    setRevoking(null);
    void queries.invalidateQueries({ queryKey: ["devices", buildingId] });
    void queries.invalidateQueries({ queryKey: ["overview", buildingId] });
  };
  const register = useMutation({
    mutationFn: (homeId: string) => api<DeviceCredentials>(`/homes/${homeId}/device`, { method: "POST" }),
    onSuccess: done,
  });
  const rotate = useMutation({
    mutationFn: (id: string) => api<DeviceCredentials>(`/devices/${id}/password`, { method: "POST" }),
    onSuccess: done,
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api<Device>(`/devices/${id}/revoke`, { method: "POST" }),
    onSuccess: () => done(),
  });

  if (buildingId === null) return <NoBuilding />;
  if (devices.isPending || overview.isPending) return <Loading />;
  if (devices.error || overview.error) return <ErrorBox error={devices.error ?? overview.error} />;
  const unitOf = new Map(overview.data.rows.map((r) => [r.home_id, r.unit_no]));
  const active = devices.data.filter((d) => d.status === "active");
  const withDevice = new Set(active.map((d) => d.home_id));
  const without = overview.data.rows.filter((r) => !withDevice.has(r.home_id));
  const error = register.error ?? rotate.error ?? revoke.error;

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{t("devices.title")}</h1>
        {picker}
      </div>
      {secret && <Credentials creds={secret} onClose={() => setSecret(null)} />}
      {error && <ErrorBox error={error} />}
      <section className="card" style={{ marginBottom: "1rem" }}>
        <table className="responsive">
          <thead>
            <tr>
              <th>{t("devices.device")}</th>
              <th>{t("devices.for")}</th>
              <th>{t("owner.cols.online")}</th>
              <th>{t("devices.firmware")}</th>
              <th>{t("devices.password")}</th>
              <th><span className="sr-only">{t("devices.actions")}</span></th>
            </tr>
          </thead>
          <tbody>
            {/* Working devices first; revoked ones stay listed for the record. */}
            {[...active, ...devices.data.filter((d) => d.status !== "active")].map((d) => (
              <tr key={d.id} className={d.status === "revoked" ? "revoked" : undefined}>
                <td data-label={t("devices.device")}><code>{d.id}</code></td>
                <td data-label={t("devices.for")}>{d.kind === "tank" ? t("tank.title") : t("owner.unit", { unit: unitOf.get(d.home_id ?? "") ?? "?" })}</td>
                <td data-label={t("owner.cols.online")}>
                  {d.status === "revoked" ? (
                    <span className="badge">{t("devices.revoked", { when: fmt.date(d.revoked_at) })}</span>
                  ) : d.online ? (
                    <span className="badge ok">● {t("owner.online")}</span>
                  ) : (
                    <span className="badge bad">○ {t("devices.lastSeen", { when: d.last_seen_at ? fmt.dateTime(d.last_seen_at) : t("devices.never") })}</span>
                  )}
                </td>
                <td data-label={t("devices.firmware")}>{d.fw_version ?? "-"}</td>
                <td data-label={t("devices.password")}>
                  {d.has_own_password ? t("devices.ownPassword", { when: fmt.date(d.credentials_changed_at ?? d.created_at) }) : t("devices.simPassword")}
                </td>
                <td>
                  {d.status === "active" && (
                    revoking === d.id ? (
                      <span className="row">
                        <button className="danger" onClick={() => revoke.mutate(d.id)} disabled={revoke.isPending}>{t("devices.confirmRevoke")}</button>
                        <button onClick={() => setRevoking(null)}>{t("app.cancel")}</button>
                      </span>
                    ) : (
                      <span className="row">
                        <button onClick={() => rotate.mutate(d.id)} disabled={rotate.isPending}>{t("devices.rotate")}</button>
                        <button onClick={() => setRevoking(d.id)}>{t("devices.revoke")}</button>
                      </span>
                    )
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {without.length > 0 && (
        <section className="card">
          <h2>{t("devices.withoutMeter")}</h2>
          <ul className="list">
            {without.map((r) => (
              <li key={r.home_id} className="row" style={{ justifyContent: "space-between" }}>
                {t("owner.unit", { unit: r.unit_no })}
                <button onClick={() => register.mutate(r.home_id)} disabled={register.isPending}>{t("devices.register")}</button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

/** The new login for a device, shown once, with a QR code for the setup app. */
function Credentials({ creds, onClose }: { creds: DeviceCredentials; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <section className="card notice warn" role="alert" style={{ marginBottom: "1rem" }}>
      <h2>{t("devices.newPassword", { id: creds.device.id })}</h2>
      <p>{t("devices.onceOnly")}</p>
      <div className="row" style={{ alignItems: "flex-start" }}>
        {/* The QR code is drawn by our own API (no outside service). */}
        <div className="qr" dangerouslySetInnerHTML={{ __html: creds.qr_svg }} />
        <dl className="codes" style={{ gridTemplateColumns: "auto 1fr" }}>
          <dt>{t("devices.username")}</dt>
          <dd><code>{creds.username}</code></dd>
          <dt>{t("devices.password")}</dt>
          <dd><code style={{ overflowWrap: "anywhere" }}>{creds.password}</code></dd>
          <dt>{t("devices.broker")}</dt>
          <dd><code>{creds.broker.host}:{creds.broker.port}</code></dd>
        </dl>
      </div>
      <button className="primary" onClick={onClose}>{t("devices.saved")}</button>
    </section>
  );
}
