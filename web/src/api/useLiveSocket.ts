/** `/ws/live` hook: connect, reconnect with exponential backoff, feed the
 * pure `liveReducer`. Exposes live counters + the last ~20 crossings. */

import { useEffect, useReducer, useRef } from "react";

import { withToken } from "./auth";
import { initialLiveState, liveReducer, type LiveState } from "./liveReducer";
import type { LiveMessage } from "./types";

const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 30_000;

export function liveSocketUrl(loc: { protocol: string; host: string } = window.location): string {
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  // WebSockets cannot send headers: a stored API token rides along as ?token=.
  return withToken(`${proto}//${loc.host}/ws/live`);
}

export function useLiveSocket(enabled = true): LiveState {
  const [state, dispatch] = useReducer(liveReducer, initialLiveState);
  const attemptRef = useRef(0);

  useEffect(() => {
    if (!enabled || typeof WebSocket === "undefined") return;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(liveSocketUrl());
      socket.onopen = () => {
        attemptRef.current = 0;
        dispatch({ type: "socket", connected: true });
      };
      socket.onmessage = (raw: MessageEvent<string>) => {
        try {
          const message = JSON.parse(raw.data) as LiveMessage;
          dispatch({ type: "message", message });
        } catch {
          // malformed frame: ignore
        }
      };
      socket.onclose = () => {
        dispatch({ type: "socket", connected: false });
        if (disposed) return;
        const backoff = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attemptRef.current);
        attemptRef.current += 1;
        timer = setTimeout(connect, backoff);
      };
      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();
    return () => {
      disposed = true;
      if (timer !== null) clearTimeout(timer);
      socket?.close();
    };
  }, [enabled]);

  return state;
}
