// Data used by several screens, kept fresh by the live connection.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "./api";
import { useMe } from "./auth";
import { useLiveEvents } from "./live";
import type { HomeLive, Reading } from "./types";

const HOME_KEY = "aquapulse.home";

/** The resident's home (the first one, or the one they picked if they have several). */
export function useMyHomeId(): [string | null, (id: string) => void] {
  const me = useMe();
  const [picked, setPicked] = useState<string | null>(() => sessionStorage.getItem(HOME_KEY));
  const id = picked && me.home_ids.includes(picked) ? picked : (me.home_ids[0] ?? null);
  return [
    id,
    (next) => {
      sessionStorage.setItem(HOME_KEY, next);
      setPicked(next);
    },
  ];
}

/** The home's latest reading, valve and lock. New readings arrive live (FR-54). */
export function useHomeLive(homeId: string | null) {
  const queries = useQueryClient();
  const key = ["live", homeId];
  const live = useQuery({
    queryKey: key,
    queryFn: () => api<HomeLive>(`/homes/${homeId}/live`),
    enabled: homeId !== null,
  });
  useLiveEvents((event) => {
    if (!homeId || event.homeId !== homeId) return;
    if (event.type === "telemetry") {
      // Put the new reading straight into the screen, no request needed.
      queries.setQueryData<HomeLive>(key, (old) =>
        old
          ? {
              ...old,
              online: true,
              last_seen_at: event.time ?? old.last_seen_at,
              valve: event.valve ?? old.valve,
              reading: {
                time: event.time ?? new Date().toISOString(),
                total_l: event.totalL ?? 0,
                delta_l: event.deltaL ?? 0,
                flow_lpm: event.flowLpm ?? 0,
                ph: event.ph ?? 0,
                temp_c: event.tempC ?? 0,
                valve: event.valve ?? "",
                rssi: old.reading?.rssi ?? 0,
              } satisfies Reading,
            }
          : old,
      );
      // Today's and this month's litres change with every reading.
      void queries.invalidateQueries({ queryKey: ["usage-today", homeId] });
    } else if (event.type === "command" || event.type === "device") {
      void queries.invalidateQueries({ queryKey: key });
    }
  });
  return live;
}
