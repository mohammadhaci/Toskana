/** Events page filter state <-> server query string (deterministic). */

import { toQueryString } from "../api/client";
import type { Direction } from "../api/types";

export interface EventFilters {
  from_ts: number | null;
  to_ts: number | null;
  camera_id: number | null;
  category_id: number | null;
  direction: Direction | null;
  canonical_only: boolean;
  q: string;
  limit: number;
  offset: number;
}

export const defaultEventFilters: EventFilters = {
  from_ts: null,
  to_ts: null,
  camera_id: null,
  category_id: null,
  direction: null,
  canonical_only: true,
  q: "",
  limit: 50,
  offset: 0,
};

/** Serialize filters for `/restaurants/{rid}/events` — defaults are elided,
 * `canonical_only` is always explicit (its server default is true, but being
 * explicit keeps the CSV-export link and the table in lockstep). */
export function filtersToQuery(filters: EventFilters, includePaging = true): string {
  return toQueryString({
    camera_id: filters.camera_id,
    canonical_only: filters.canonical_only,
    category_id: filters.category_id,
    direction: filters.direction,
    from_ts: filters.from_ts,
    limit: includePaging ? filters.limit : undefined,
    offset: includePaging && filters.offset > 0 ? filters.offset : undefined,
    q: filters.q.trim() || undefined,
    to_ts: filters.to_ts,
  });
}

/** datetime-local input value -> epoch ms (null on empty/invalid). */
export function localInputToMs(value: string): number | null {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/** epoch ms -> datetime-local input value (local time, minute precision). */
export function msToLocalInput(ms: number | null): string {
  if (ms === null) return "";
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
