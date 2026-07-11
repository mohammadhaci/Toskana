# Line-crossing engine

`src/toskana/vision/line_crossing.py` is the counting core: it converts object
trajectories into **one event per physical crossing** — robust against jitter,
loitering, detection dropouts and tracker ID switches.

## Geometry

- The **anchor point** of a detection is the bottom-center of its bounding box
  (where the object touches the counter/hands, the most stable point under
  perspective).
- Lines are stored **normalized (0..1)** and scaled to pixels per frame. The side of
  the line an anchor is on is the sign of the cross product with the line direction
  A→B; **positive = kitchen → customers ("out")**, negative = the return direction
  ("in"). Flipping A and B flips the direction — the Line Editor shows the arrow.

## Per-(track, line) state machine

```
NEW ──(track_age ≥ min_track_age)──▶ TENTATIVE ──(clearly on one side)──▶ ARMED(side)
ARMED ──(confirmed side change + segment intersection)──▶ CROSSED ──(cooldown)──▶ ARMED(new side)
```

- `min_track_age` (default 3 frames): brand-new tracks cannot count — kills
  detector flicker.
- **Hysteresis** (`hysteresis_px`, default 12 px at 640 width, scaled with
  resolution): a neutral corridor around the line. An anchor inside it changes
  nothing, so an item **placed on the counter and wobbling** on the line
  (`loiter_on_line` scenario) counts once, not repeatedly.
- **Two-frame confirmation**: the new side must hold for 2 consecutive frames.
- **Segment intersection**: the anchor's movement segment must actually intersect
  the finite line segment A→B — walking *around* the counter does not count.
- **Cooldown** (`cooldown_ms`, default 1500): after a crossing, the same (track,
  line) cannot count again until it expires, then the state re-arms on the new side.

All three tuning parameters are configurable **per line** in the dashboard.

## Robustness

- **Coasting**: when detection drops out mid-crossing (occlusion), the tracker
  coasts the track for up to `max_coast_frames`; the state machine freezes instead
  of resetting (`occlusion_gap` scenario).
- **ID-switch guard**: a ring buffer of the last K events per line suppresses a new
  event with the *same line, direction and class* within 60 px and 1200 ms of a
  previous one — the classic "track died and reappeared under a new id" double
  count. Suppressions are recorded in the counter stats (`id_switch`) for tuning.
  Tray items are unaffected: they cross **simultaneously at different anchors**,
  not sequentially at the same one.

## Multi-item carries

A tray with 3 items is 3 detections → 3 tracks → 3 independent state machines → 3
events. This is a **release gate**: `tests/integration/test_counting_scenarios.py::
TestScenariosExactCounts::test_tray_carry_3` must count exactly 3.

## Guarantees under test

Every synthetic scenario asserts exact counts against `ground_truth.json`
(`single_drink`=1, `tray_carry_3`=3, `reverse_return`=1 out + 1 in,
`loiter_on_line`=1, `occlusion_gap`=1 even with a forced ID switch), plus a
Hypothesis property test: random straight-line trajectories never count twice.
