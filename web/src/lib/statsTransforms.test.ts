import { describe, expect, it } from "vitest";

import type { TimeseriesRow } from "../api/types";
import { grandTotals, presetBounds, toStackedSeries, totalsByGroup } from "./statsTransforms";

function row(bucket_ts: number, group: string | null, out: number, in_: number): TimeseriesRow {
  return { bucket_ts, bucket_iso: new Date(bucket_ts).toISOString(), group, out, in: in_, net: out - in_ };
}

describe("toStackedSeries", () => {
  it("pivots rows into one point per bucket with zero-fill", () => {
    const rows = [row(1000, "1", 5, 1), row(1000, "2", 3, 0), row(2000, "1", 2, 0)];
    const { points, groups } = toStackedSeries(rows, "out");
    expect(groups).toEqual(["1", "2"]);
    expect(points).toHaveLength(2);
    expect(points[0]).toMatchObject({ bucket_ts: 1000, "1": 5, "2": 3 });
    // group "2" missing in bucket 2000 -> filled with 0 (no stacked-area tearing)
    expect(points[1]).toMatchObject({ bucket_ts: 2000, "1": 2, "2": 0 });
  });

  it("sorts buckets ascending and supports the net metric", () => {
    const rows = [row(3000, "a", 4, 1), row(1000, "a", 2, 2)];
    const { points } = toStackedSeries(rows, "net");
    expect(points.map((p) => p.bucket_ts)).toEqual([1000, 3000]);
    expect(points[0].a).toBe(0);
    expect(points[1].a).toBe(3);
  });

  it("maps null group to the ungrouped sentinel", () => {
    const { groups } = toStackedSeries([row(1000, null, 1, 0)]);
    expect(groups).toEqual(["__none__"]);
  });
});

describe("totals", () => {
  it("computes per-group totals with net = out - in", () => {
    const rows = [row(1000, "1", 5, 2), row(2000, "1", 3, 1), row(1000, "2", 7, 0)];
    const totals = totalsByGroup(rows);
    expect(totals).toEqual([
      { group: "1", out: 8, in: 3, net: 5 },
      { group: "2", out: 7, in: 0, net: 7 },
    ]);
  });

  it("computes grand totals", () => {
    const rows = [row(1000, "1", 5, 2), row(1000, "2", 7, 3)];
    expect(grandTotals(rows)).toEqual({ out: 12, in: 5, net: 7 });
  });

  it("handles empty input", () => {
    expect(totalsByGroup([])).toEqual([]);
    expect(grandTotals([])).toEqual({ out: 0, in: 0, net: 0 });
  });
});

describe("presetBounds", () => {
  const now = new Date(2026, 6, 10, 14, 30); // local 2026-07-10 14:30

  it("today spans the local day", () => {
    const { from_ts, to_ts } = presetBounds("today", now);
    expect(new Date(from_ts).getHours()).toBe(0);
    expect(to_ts - from_ts).toBe(24 * 3600 * 1000);
    expect(new Date(from_ts).getDate()).toBe(10);
  });

  it("yesterday ends where today starts", () => {
    const today = presetBounds("today", now);
    const yesterday = presetBounds("yesterday", now);
    expect(yesterday.to_ts).toBe(today.from_ts);
    expect(yesterday.to_ts - yesterday.from_ts).toBe(24 * 3600 * 1000);
  });

  it("last7 covers seven local days ending today", () => {
    const { from_ts, to_ts } = presetBounds("last7", now);
    expect(to_ts - from_ts).toBe(7 * 24 * 3600 * 1000);
    expect(to_ts).toBe(presetBounds("today", now).to_ts);
  });
});
