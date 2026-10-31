// Turning the water on or off (FR-44, FR-51). The button only asks; the
// device answers. We show "Waiting for the valve…" until the device confirms,
// then what really happened, or a clear message if it failed or took too long.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { useLiveEvents } from "../live";
import type { Command, HomeLive } from "../types";
import { ErrorBox } from "./ui";

const FINAL = new Set(["succeeded", "failed", "rejected", "timeout"]);

export function ValveControl({ live, unit }: { live: HomeLive; unit: string }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [confirm, setConfirm] = useState<"open" | "close" | null>(null);
  const [commandId, setCommandId] = useState<string | null>(live.pending_command?.id ?? null);

  const send = useMutation({
    mutationFn: (action: "open" | "close") =>
      api<Command>(`/homes/${live.home_id}/valve`, { method: "POST", body: { action, reason: "" } }),
    onMutate: () => setCommandId(null), // forget the answer to the previous command
    onSuccess: (command) => {
      setCommandId(command.id);
      setConfirm(null);
    },
  });
  const command = useQuery({
    queryKey: ["command", commandId],
    queryFn: () => api<Command>(`/commands/${commandId}`),
    enabled: commandId !== null,
    // Ask again every second until the device has answered (the live event is usually faster).
    refetchInterval: (query) => (query.state.data && FINAL.has(query.state.data.state) ? false : 1000),
  });
  useLiveEvents((event) => {
    if (event.type === "command" && event.commandId === commandId) {
      void command.refetch();
      void queries.invalidateQueries({ queryKey: ["live", live.home_id] });
    }
  });

  const state = command.data?.state;
  // Once the device has answered, read the valve's new position.
  useEffect(() => {
    if (state && FINAL.has(state)) void queries.invalidateQueries({ queryKey: ["live", live.home_id] });
  }, [state, queries, live.home_id]);
  const waiting = commandId !== null && (!state || !FINAL.has(state));
  const valve = live.valve ?? "unknown";
  const isOpen = valve === "open";
  const label = ["open", "closed", "moving", "fault"].includes(valve) ? valve : "unknown";

  if (live.device_id === null) return <p className="muted">{t("home.noDevice")}</p>;

  return (
    <div>
      <p className="stat">
        <span className="value" style={{ fontSize: "1.25rem" }}>
          {t(`valve.${label}`)}
        </span>
      </p>
      {live.lock ? (
        <div className="notice warn" role="status">
          <strong>🔒 {t("valve.locked", { reason: live.lock.reason })}</strong>
          <p className="small" style={{ margin: 0 }}>{t("valve.lockedHelp")}</p>
        </div>
      ) : !live.online ? (
        <p className="notice warn">{t("valve.offline")}</p>
      ) : confirm ? (
        <div className="notice info" role="alertdialog" aria-labelledby="valve-confirm">
          <p id="valve-confirm">
            {t(confirm === "close" ? "valve.confirmOff" : "valve.confirmOn", { unit })}
          </p>
          <div className="row">
            <button className={confirm === "close" ? "danger" : "primary"} disabled={send.isPending}
                    onClick={() => send.mutate(confirm)} autoFocus>
              {t("valve.yes")}
            </button>
            <button onClick={() => setConfirm(null)}>{t("app.cancel")}</button>
          </div>
        </div>
      ) : (
        <button
          className={isOpen ? "danger" : "primary"}
          disabled={waiting || valve === "moving"}
          onClick={() => setConfirm(isOpen ? "close" : "open")}
        >
          {t(isOpen ? "valve.turnOff" : "valve.turnOn")}
        </button>
      )}
      <div aria-live="polite" style={{ marginTop: ".5rem" }}>
        {waiting && <p className="muted">{t("valve.pending")}</p>}
        {state === "succeeded" && <p className="notice ok">{t("valve.succeeded")}</p>}
        {state === "failed" && (
          <p className="notice bad">{t("valve.failed", { reason: command.data?.result_reason ?? "?" })}</p>
        )}
        {state === "rejected" && (
          <p className="notice bad">{t("valve.rejected", { reason: command.data?.result_reason ?? "?" })}</p>
        )}
        {state === "timeout" && <p className="notice bad">{t("valve.timeout")}</p>}
      </div>
      {send.error && <ErrorBox error={send.error} />}
    </div>
  );
}
