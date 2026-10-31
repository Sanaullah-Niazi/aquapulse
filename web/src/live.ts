// The live connection (FR-25, FR-54): one WebSocket for the whole app.
// Readings, valve answers, alerts and bill changes arrive the moment they
// happen, so screens update without reloading. If the connection drops (Wi-Fi,
// server restart), it comes back by itself, waiting a bit longer each time.
import { useEffect, useRef, useSyncExternalStore } from "react";
import { api } from "./api";
import { websocketUrl } from "./config";
import type { LiveEvent } from "./types";

type Listener = (event: LiveEvent) => void;
export type LiveStatus = "off" | "connecting" | "live";

const listeners = new Set<Listener>();
const statusListeners = new Set<() => void>();
let socket: WebSocket | null = null;
let status: LiveStatus = "off";
let wanted = false;
let retry = 0;
let timer: ReturnType<typeof setTimeout> | undefined;

function setStatus(next: LiveStatus) {
  status = next;
  statusListeners.forEach((notify) => notify());
}

async function connect() {
  if (!wanted || socket) return;
  setStatus("connecting");
  try {
    // A one-time ticket, because browsers cannot send a token header on a WebSocket.
    const { ticket } = await api<{ ticket: string }>("/ws-ticket", { method: "POST" });
    if (!wanted) return;
    const ws = new WebSocket(websocketUrl(ticket));
    socket = ws;
    ws.onmessage = (message) => {
      const event = JSON.parse(message.data) as LiveEvent;
      if (event.type === "ready") {
        retry = 0;
        setStatus("live");
      }
      listeners.forEach((listener) => listener(event));
    };
    ws.onclose = () => {
      socket = null;
      setStatus(wanted ? "connecting" : "off");
      scheduleReconnect();
    };
  } catch {
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (!wanted) return;
  clearTimeout(timer);
  const wait = Math.min(30_000, 1000 * 2 ** retry++); // 1 s, 2 s, 4 s ... at most 30 s
  timer = setTimeout(connect, wait);
}

/** Open the connection after login; close it on logout. */
export function setLive(on: boolean): void {
  wanted = on;
  if (on) void connect();
  else {
    clearTimeout(timer);
    socket?.close();
    socket = null;
    setStatus("off");
  }
}

/** Call `handler` for every live event while the component is on screen. */
export function useLiveEvents(handler: Listener): void {
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    const listener: Listener = (event) => latest.current(event);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);
}

export function useLiveStatus(): LiveStatus {
  return useSyncExternalStore(
    (notify) => {
      statusListeners.add(notify);
      return () => statusListeners.delete(notify);
    },
    () => status,
  );
}
