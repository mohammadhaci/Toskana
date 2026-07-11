/** Pure reducer behind `useLiveSocket` — unit-testable without a WebSocket.
 *
 * `hello` seeds today's counters; each `crossing` increments the matching
 * category counter (net = out - in) and prepends the event to the ticker.
 * Unknown message types (e.g. the future `correction`) are ignored
 * gracefully; a `dedup_correction` demotion decrements when it carries
 * enough context (category_id + direction).
 */

import type { LiveCounter, LiveCrossingEvent, LiveGap, LiveMessage } from "./types";

export const TICKER_LIMIT = 20;

export interface LiveState {
  connected: boolean;
  ready: boolean;
  counters: LiveCounter[];
  events: LiveCrossingEvent[];
  gaps: LiveGap[];
}

export const initialLiveState: LiveState = {
  connected: false,
  ready: false,
  counters: [],
  events: [],
  gaps: [],
};

export type LiveAction =
  | { type: "socket"; connected: boolean }
  | { type: "message"; message: LiveMessage };

function counterKey(categoryId: number | null): string {
  return categoryId === null ? "null" : String(categoryId);
}

function bump(counters: LiveCounter[], categoryId: number | null, direction: string, delta: 1 | -1): LiveCounter[] {
  const key = counterKey(categoryId);
  const index = counters.findIndex((c) => counterKey(c.category_id) === key);
  const list = [...counters];
  const base: LiveCounter =
    index >= 0
      ? { ...list[index] }
      : {
          category_id: categoryId,
          key: null,
          name_de: null,
          name_en: null,
          color_hex: null,
          out: 0,
          in: 0,
          net: 0,
        };
  if (direction === "out") base.out = Math.max(0, base.out + delta);
  else if (direction === "in") base.in = Math.max(0, base.in + delta);
  else return counters; // unknown direction: leave state untouched
  base.net = base.out - base.in;
  if (index >= 0) list[index] = base;
  else list.push(base);
  return list;
}

export function liveReducer(state: LiveState, action: LiveAction): LiveState {
  if (action.type === "socket") {
    return { ...state, connected: action.connected };
  }
  const message = action.message;
  switch (message.type) {
    case "hello": {
      const counters = Array.isArray(message.counters) ? (message.counters as LiveCounter[]) : [];
      return { ...state, ready: true, counters };
    }
    case "crossing": {
      const event = message.event as LiveCrossingEvent | undefined;
      if (!event || typeof event !== "object") return state;
      return {
        ...state,
        counters: bump(state.counters, event.category_id ?? null, event.direction, 1),
        events: [event, ...state.events].slice(0, TICKER_LIMIT),
      };
    }
    case "gap": {
      const gap = (message as { gap?: LiveGap }).gap;
      if (!gap) return state;
      return { ...state, gaps: [gap, ...state.gaps].slice(0, TICKER_LIMIT) };
    }
    case "dedup_correction": {
      // Future M8 message: only adjust when it carries enough context.
      const m = message as { category_id?: number | null; direction?: string; is_canonical?: boolean };
      if (m.is_canonical === false && m.direction && m.category_id !== undefined) {
        return { ...state, counters: bump(state.counters, m.category_id, m.direction, -1) };
      }
      return state;
    }
    case "refined": {
      // AI Event Refiner corrected an event's category: move the count.
      const m = message as {
        category_id?: number | null;
        previous_category_id?: number | null;
        direction?: string;
      };
      if (m.direction && m.category_id !== undefined && m.previous_category_id !== undefined && m.category_id !== m.previous_category_id) {
        const counters = bump(
          bump(state.counters, m.previous_category_id, m.direction, -1),
          m.category_id,
          m.direction,
          1,
        );
        return { ...state, counters };
      }
      return state;
    }
    default:
      return state; // unknown message types are ignored gracefully
  }
}
