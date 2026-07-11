# Testing & on-site validation

## Automated suite (CI, every PR)

`make test` runs deterministically without GPU, weights or network:

- **Unit**: geometry, line-crossing state machine (+ Hypothesis property tests),
  dedup matching, class mapping, drift detector, DB models/migrations, timezone/DST
  aggregation, CSV golden files.
- **Integration**: every synthetic scenario replayed end-to-end with **exact**
  assertions against `ground_truth.json` (`single_drink`, `tray_carry_3` — release
  gate, `reverse_return`, `loiter_on_line`, `occlusion_gap`, `two_cam_same_exit`),
  plus the eval harness scoring 100% on all of them
  (`tests/integration/test_eval_counting.py`).
- **API**: every route, tenant isolation, WebSocket latency, MJPEG, retention,
  calibrate/drift.
- **Frontend**: vitest + i18n key parity (de/en) + tsc + production build.
- Markers outside CI: `-m ml` (real YOLO inference), `-m slow` (smoke training).

## The evaluation harness

```bash
toskana eval-counting --video clip.mp4 --gt ground_truth.json \
    --line 0.5,0,0.5,1 --backend yolo --db toskana.db --min-score 0.9
```

Replays the clip through the real pipeline and reports **precision / recall / F1 /
count MAE — per category and per hour of day** (peak-hour accuracy is visible
separately from the average). Two-camera exits: add `--video2/--line2`; canonical
counts after dedup are scored. `--db` persists the run into `counting_eval_runs`;
`--min-score` makes the command fail for CI/acceptance scripts.

Ground-truth format: a JSON with `crossings` (or `canonical_crossings` for
two-camera clips), each `{class_key, direction: out|in, crossing_ts_ms}` in media
time. The synthetic generator (`tests/tools/make_synthetic_video.py`) emits
reference files to copy from.

## On-site validation checklist (per installation)

Run before hand-over, and after any camera or menu change:

1. **Placement sanity** — camera angle and line per
   [deployment.md](deployment.md#camera-placement); confirm tray items appear as
   separate boxes in the live preview.
2. **Calibrate** — `POST /api/cameras/{id}/calibrate` once the final mounting is
   fixed; verify drift status is green on the System page.
3. **Annotated footage** — record **≥ 30 minutes** of real service *including the
   peak hour*, annotate every crossing (category, direction, timestamp) into the
   ground-truth format.
4. **Accuracy gate** — `toskana eval-counting … --min-score 0.9`: **≥ 90%
   precision and recall (Phase 1)** overall **and in the peak-hour bucket**
   (`per_hour` in the report). Investigate every FP/FN before tuning thresholds.
5. **Dedup check** (multi-camera exits) — walk 3 items through the pass; verify 3
   canonical events, the duplicates visible-but-demoted in the Events page.
6. **RTSP soak — 24 h**: leave all pipelines running for a full day. Afterwards
   verify on the System page / `data_gaps`: no unexplained gaps, stable FPS ≥ 8,
   no drift alarms, DB size growth as expected, reconnects (if any) logged with
   matching gap records.
7. **POS reconciliation** — import one day's POS CSV on the Reconciliation page;
   review per-category deviation with the manager.
8. **Retention** — confirm `snapshot_retention_days` matches the works-council
   agreement and run `POST /api/system/retention/run` once.

**Phase 2 gate**: a fine-tuned menu-item model needs per-item mAP50 ≥ 0.6 on its
validation set before activation ([training.md](training.md)), then re-run steps
3–4 with item-level ground truth.
