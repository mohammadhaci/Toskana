# Cross-camera deduplication

When one exit is covered by several cameras, the same physical crossing produces one
raw event **per camera**. The DedupEngine (`src/toskana/vision/dedup.py`) makes sure
it is *counted* once — without ever deleting data.

## Model

- Cameras covering the same physical pass are grouped in an **exit group**
  (`exit_groups` table; a camera belongs to at most one group).
- Every raw event is **always stored**. Within a dedup group exactly one event is
  `is_canonical = true`; statistics, live counters and reconciliation count
  canonical events only. Duplicates remain queryable for audit and re-tuning.

## Matching

Two events form a duplicate pair when **all** of the following hold:

1. different cameras of the same exit group,
2. same direction,
3. same identity (category / menu item — different categories are *never* merged),
4. timestamps within the group's `dedup_window_ms` (default 2000 ms),
5. greedy **one-to-one** assignment, oldest first — three plates seen by two cameras
   form three dedup groups and a canonical count of three, never fewer.

Which event stays canonical is the group's `dedup_strategy`:

- `primary_wins` (default): the event from the camera flagged
  `is_primary_in_group`; falls back to first-seen when the primary saw nothing.
- `first_wins`: the earliest event.

## Optimistic writes and corrections

Events are written canonical immediately (live counters must not wait for the dedup
window to close). When a later event completes a pair, the engine:

1. publishes `demote` on the bus — the EventWriter applies the
   `is_canonical=false` + `dedup_group_id` UPDATE (single-writer discipline),
2. publishes `dedup_correction` — forwarded on `/ws/live` as
   `{type: "correction", event_id, dedup_group_id, ...}` so dashboards decrement
   the affected counter without a reload.

## Manual review

The Events page shows dedup groups with their suppressed duplicates. Operators can
**unmerge** (re-promote a demoted event) or **confirm** a merge — `is_canonical` is
editable through the API, and every change stays in the append-only event log.

## Known limits

- Camera clocks matter: skew larger than the window breaks matching. The site runs
  on one machine (one clock); pipelines timestamp on processing, and a startup
  warning fires on suspicious skew.
- Two *genuinely distinct* same-category items crossing on different cameras within
  the window are merged — the window is deliberately short (2 s) to make this
  unlikely, and the pair remains reviewable. Conversely, the same item seen > window
  apart (documented limit: ≥ 3 s) produces two events; the boundary is covered by
  tests (`tests/unit/test_dedup.py`, `tests/integration/test_dedup_scenarios.py`).
