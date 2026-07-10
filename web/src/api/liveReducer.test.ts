import { describe, expect, it } from "vitest";

import { initialLiveState, liveReducer, TICKER_LIMIT, type LiveState } from "./liveReducer";
import type { LiveCounter, LiveCrossingEvent } from "./types";

function counter(categoryId: number | null, out: number, in_: number): LiveCounter {
  return {
    category_id: categoryId,
    key: categoryId === null ? null : `cat-${categoryId}`,
    name_de: null,
    name_en: null,
    color_hex: "#123456",
    out,
    in: in_,
    net: out - in_,
  };
}

function crossing(categoryId: number | null, direction: "out" | "in", id = "e1"): LiveCrossingEvent {
  return {
    id,
    restaurant_id: 1,
    camera_id: 1,
    line_id: 1,
    track_id: 7,
    category_id: categoryId,
    menu_item_id: null,
    raw_class_name: "cup",
    confidence: 0.9,
    direction,
    ts: 1720000000000,
    frame_index: 10,
    anchor_x: 0.5,
    anchor_y: 0.5,
    snapshot_path: null,
  };
}

function withMessage(state: LiveState, message: unknown): LiveState {
  return liveReducer(state, { type: "message", message: message as never });
}

describe("liveReducer", () => {
  it("hello sets the counters and marks the state ready", () => {
    const state = withMessage(initialLiveState, {
      type: "hello",
      counters: [counter(1, 5, 2), counter(null, 1, 0)],
    });
    expect(state.ready).toBe(true);
    expect(state.counters).toHaveLength(2);
    expect(state.counters[0]).toMatchObject({ out: 5, in: 2, net: 3 });
  });

  it("crossing increments the matching category and direction", () => {
    let state = withMessage(initialLiveState, { type: "hello", counters: [counter(1, 5, 2)] });
    state = withMessage(state, { type: "crossing", event: crossing(1, "out") });
    expect(state.counters[0]).toMatchObject({ out: 6, in: 2, net: 4 });

    state = withMessage(state, { type: "crossing", event: crossing(1, "in", "e2") });
    expect(state.counters[0]).toMatchObject({ out: 6, in: 3, net: 3 });
  });

  it("crossing for an unseen category creates a counter", () => {
    let state = withMessage(initialLiveState, { type: "hello", counters: [] });
    state = withMessage(state, { type: "crossing", event: crossing(9, "out") });
    expect(state.counters).toHaveLength(1);
    expect(state.counters[0]).toMatchObject({ category_id: 9, out: 1, in: 0, net: 1 });
  });

  it("crossing with null category bumps the unmapped counter", () => {
    let state = withMessage(initialLiveState, { type: "hello", counters: [counter(null, 2, 0)] });
    state = withMessage(state, { type: "crossing", event: crossing(null, "out") });
    expect(state.counters[0]).toMatchObject({ category_id: null, out: 3, net: 3 });
  });

  it("prepends crossings to the ticker capped at the limit", () => {
    let state = withMessage(initialLiveState, { type: "hello", counters: [] });
    for (let i = 0; i < TICKER_LIMIT + 5; i++) {
      state = withMessage(state, { type: "crossing", event: crossing(1, "out", `e${i}`) });
    }
    expect(state.events).toHaveLength(TICKER_LIMIT);
    expect(state.events[0].id).toBe(`e${TICKER_LIMIT + 4}`); // newest first
  });

  it("ignores unknown message types gracefully", () => {
    const seeded = withMessage(initialLiveState, { type: "hello", counters: [counter(1, 5, 2)] });
    const state = withMessage(seeded, { type: "correction", whatever: true });
    expect(state).toEqual(seeded);
    const state2 = withMessage(seeded, { type: "totally_new_thing" });
    expect(state2).toEqual(seeded);
  });

  it("ignores malformed crossing payloads", () => {
    const seeded = withMessage(initialLiveState, { type: "hello", counters: [counter(1, 5, 2)] });
    expect(withMessage(seeded, { type: "crossing" })).toEqual(seeded);
  });

  it("applies a dedup_correction demotion when context is present", () => {
    const seeded = withMessage(initialLiveState, { type: "hello", counters: [counter(1, 5, 2)] });
    const state = withMessage(seeded, {
      type: "dedup_correction",
      is_canonical: false,
      category_id: 1,
      direction: "out",
    });
    expect(state.counters[0]).toMatchObject({ out: 4, in: 2, net: 2 });
  });

  it("records gaps capped at the limit", () => {
    const state = withMessage(initialLiveState, {
      type: "gap",
      gap: { restaurant_id: 1, camera_id: 2, from_ts: 1, to_ts: 2, reason: "reconnect" },
    });
    expect(state.gaps).toHaveLength(1);
  });

  it("socket action toggles connected without touching data", () => {
    const seeded = withMessage(initialLiveState, { type: "hello", counters: [counter(1, 1, 0)] });
    const state = liveReducer(seeded, { type: "socket", connected: true });
    expect(state.connected).toBe(true);
    expect(state.counters).toEqual(seeded.counters);
  });
});
