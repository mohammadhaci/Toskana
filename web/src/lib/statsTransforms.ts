/** Pure transforms from `/stats/timeseries` rows into chart-ready shapes. */

import type { TimeseriesRow } from "../api/types";

export type Metric = "out" | "in" | "net";

export interface StackedPoint {
  bucket_ts: number;
  bucket_iso: string;
  [groupKey: string]: number | string;
}

const UNGROUPED = "__none__";

export function groupKeyOf(row: TimeseriesRow): string {
  return row.group === null ? UNGROUPED : row.group;
}

/** Pivot rows into one point per bucket with a column per group key.
 * Missing (bucket, group) combinations are filled with 0 so stacked areas
 * don't tear. Points are sorted by bucket_ts; group keys sorted for a
 * stable series order. */
export function toStackedSeries(
  rows: TimeseriesRow[],
  metric: Metric = "out",
): { points: StackedPoint[]; groups: string[] } {
  const groups = Array.from(new Set(rows.map(groupKeyOf))).sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true }),
  );
  const byBucket = new Map<number, StackedPoint>();
  for (const row of rows) {
    let point = byBucket.get(row.bucket_ts);
    if (!point) {
      point = { bucket_ts: row.bucket_ts, bucket_iso: row.bucket_iso };
      for (const g of groups) point[g] = 0;
      byBucket.set(row.bucket_ts, point);
    }
    point[groupKeyOf(row)] = row[metric];
  }
  const points = Array.from(byBucket.values()).sort((a, b) => (a.bucket_ts as number) - (b.bucket_ts as number));
  // Ensure every group column exists on every point (first row of a bucket
  // may have been created before all groups were known — they are, since we
  // precompute groups, but keep the invariant explicit for safety).
  for (const point of points) {
    for (const g of groups) if (!(g in point)) point[g] = 0;
  }
  return { points, groups };
}

export interface GroupTotals {
  group: string;
  out: number;
  in: number;
  net: number;
}

/** Sum out/in per group across all buckets; net = out - in. */
export function totalsByGroup(rows: TimeseriesRow[]): GroupTotals[] {
  const map = new Map<string, GroupTotals>();
  for (const row of rows) {
    const key = groupKeyOf(row);
    const entry = map.get(key) ?? { group: key, out: 0, in: 0, net: 0 };
    entry.out += row.out;
    entry.in += row.in;
    entry.net = entry.out - entry.in;
    map.set(key, entry);
  }
  return Array.from(map.values()).sort((a, b) =>
    a.group.localeCompare(b.group, undefined, { numeric: true }),
  );
}

/** Grand totals across every row. */
export function grandTotals(rows: TimeseriesRow[]): { out: number; in: number; net: number } {
  let out = 0;
  let in_ = 0;
  for (const row of rows) {
    out += row.out;
    in_ += row.in;
  }
  return { out, in: in_, net: out - in_ };
}

export function isUngrouped(key: string): boolean {
  return key === UNGROUPED;
}

// -- time range presets --------------------------------------------------------

export type RangePreset = "today" | "yesterday" | "last7" | "custom";

/** Local-time [from, to) epoch-ms bounds for a preset (now = injection point for tests). */
export function presetBounds(preset: Exclude<RangePreset, "custom">, now = new Date()): {
  from_ts: number;
  to_ts: number;
} {
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const today = startOfDay(now);
  const dayMs = 24 * 3600 * 1000;
  switch (preset) {
    case "today":
      return { from_ts: today.getTime(), to_ts: today.getTime() + dayMs };
    case "yesterday":
      return { from_ts: today.getTime() - dayMs, to_ts: today.getTime() };
    case "last7":
      return { from_ts: today.getTime() - 6 * dayMs, to_ts: today.getTime() + dayMs };
  }
}
