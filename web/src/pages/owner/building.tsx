// The owner's building: which one is shown (an owner may have several), and
// its overview (FR-42), kept fresh by the live connection without reloading.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../api";
import { useMe } from "../../auth";
import { useLiveEvents } from "../../live";
import type { Building, Overview } from "../../types";

const KEY = "aquapulse.building";

/** The building being looked at, and a dropdown when the owner has more than one. */
export function useBuilding() {
  const { t } = useTranslation();
  const me = useMe();
  const [picked, setPicked] = useState<string | null>(() => {
    try {
      return localStorage.getItem(KEY);
    } catch {
      return null;
    }
  });
  const id = picked && me.building_ids.includes(picked) ? picked : (me.building_ids[0] ?? null);
  const buildings = useQuery({
    queryKey: ["buildings"],
    queryFn: () => api<Building[]>("/buildings"),
    staleTime: 5 * 60_000,
  });
  const building = buildings.data?.find((b) => b.id === id) ?? null;
  const pick = (next: string) => {
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // private window: remember only until the page reloads
    }
    setPicked(next);
  };
  const picker =
    (buildings.data?.length ?? 0) > 1 ? (
      <select aria-label={t("owner.building")} value={id ?? ""} style={{ width: "auto" }}
              onChange={(e) => pick(e.target.value)}>
        {buildings.data!.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
          </option>
        ))}
      </select>
    ) : null;
  return { buildingId: id, building, picker };
}

/** The building overview. New readings change the homes' numbers on the spot;
 *  alerts, valve answers, locks, devices and bills ask the API again. */
export function useOverview(buildingId: string | null) {
  const queries = useQueryClient();
  const key = ["overview", buildingId];
  const overview = useQuery({
    queryKey: key,
    queryFn: () => api<Overview>(`/buildings/${buildingId}/overview`),
    enabled: buildingId !== null,
    refetchInterval: 60_000, // litres today/this month: add up again every minute
  });
  useLiveEvents((event) => {
    if (!buildingId || event.buildingId !== buildingId) return;
    if (event.type === "telemetry") {
      queries.setQueryData<Overview>(key, (old) => {
        if (!old) return old;
        const rows = old.rows.map((row) =>
          row.home_id !== event.homeId
            ? row
            : {
                ...row,
                online: true,
                ph: event.ph ?? row.ph,
                temp_c: event.tempC ?? row.temp_c,
                flow_lpm: event.flowLpm ?? row.flow_lpm,
                valve: event.valve ?? row.valve,
                reading_at: event.time ?? row.reading_at,
                last_seen_at: event.time ?? row.last_seen_at,
                litres_today: row.litres_today + (event.deltaL ?? 0),
                litres_month: row.litres_month + (event.deltaL ?? 0),
              },
        );
        const sum = (pick: (r: (typeof rows)[number]) => number) =>
          Math.round(rows.reduce((total, r) => total + pick(r), 0) * 10) / 10;
        return {
          ...old,
          rows,
          flow_lpm: sum((r) => r.flow_lpm),
          litres_today: sum((r) => r.litres_today),
          litres_month: sum((r) => r.litres_month),
          homes_online: rows.filter((r) => r.online).length,
          valves_closed: rows.filter((r) => r.valve === "closed").length,
        };
      });
    } else if (event.type === "tank") {
      queries.setQueryData<Overview>(key, (old) =>
        old?.tank
          ? {
              ...old,
              tank: {
                ...old.tank,
                online: true,
                level_pct: (event.levelPct as number | null) ?? old.tank.level_pct,
                pump_on: (event.pumpOn as boolean) ?? old.tank.pump_on,
                mode: (event.mode as string) ?? old.tank.mode,
              },
            }
          : old,
      );
    } else if (["alert", "command", "lock", "device", "bill"].includes(event.type)) {
      void queries.invalidateQueries({ queryKey: key });
    }
  });
  return overview;
}

export function NoBuilding() {
  const { t } = useTranslation();
  return <p className="notice info">{t("owner.noBuilding")}</p>;
}
