# Architecture

Toskana runs as **one process per restaurant site**. Video is captured, analyzed and
stored locally; nothing leaves the machine. The same process serves the browser
dashboard.

```
toskana run --config config.yaml
  └── FastAPI (uvicorn) :8420 → serves web/dist (dashboard) + REST + WebSocket + MJPEG
  └── PipelineManager (threads)
        └── per camera: Capture → Detector+Tracker → LineCrossingCounter
        └── DedupEngine (per exit group) → EventWriter (single DB writer, SQLite WAL)
        └── DriftDetector (per camera, SSIM vs calibration snapshot)
  └── RetentionJob (daily snapshot cleanup, GDPR)
```

## Components

| Component | Module | Responsibility |
|---|---|---|
| Capture | `vision/capture.py` | RTSP / USB / file input, FPS pacing, reconnect with `data_gaps` records |
| Detector backends | `vision/backends/` | `DetectorBackend` protocol: `YoloBackend` (Ultralytics + ByteTrack) for production, `SyntheticShapeDetector` + `IouTracker` for deterministic CI/demo runs |
| Line crossing | `vision/line_crossing.py` | Per-(track, line) state machine that turns trajectories into counting events — see [line-crossing.md](line-crossing.md) |
| Class mapping | `vision/mapping.py` | model class → category (Phase 1) or menu item (Phase 2), with per-mapping confidence thresholds |
| Event bus | `events/bus.py` | In-process pub/sub (`crossing`, `gap`, `demote`, `dedup_correction`, `drift`) |
| Event writer | `events/writer.py` | The **only** DB writer thread; ULID event ids, per-event snapshot JPEGs |
| Dedup engine | `vision/dedup.py` | Cross-camera duplicate removal per exit group — see [dedup.md](dedup.md) |
| Pipeline manager | `vision/manager.py` | One supervised thread per camera, hot config reload, MJPEG preview frames, FPS health, drift watchdog wiring |
| Drift detector | `vision/drift.py` | Periodic SSIM against the calibration snapshot; alarms on a moved/blocked camera and flags the data as suspect |
| Retention job | `retention.py` | Deletes event snapshots older than `snapshot_retention_days` (daily + `POST /api/system/retention/run` + `toskana cleanup`) |
| Eval harness | `eval/` | `toskana eval-counting`: precision/recall/MAE vs annotated ground truth, per category and per hour — see [testing.md](testing.md) |
| API | `api/` | REST CRUD, stats, events + CSV export, POS reconciliation, `/ws/live`, MJPEG streaming, system health |
| Dashboard | `web/` | React + Vite + TypeScript, i18next (de/en), Recharts, TanStack Query; static build served by FastAPI |

## Event flow

1. A camera pipeline confirms a line crossing and publishes a `crossing` payload on
   the bus (with the event snapshot already written).
2. The **EventWriter** inserts the row (optimistically `is_canonical=true`).
3. The **DedupEngine** (if the camera belongs to a ≥2-camera exit group) may match it
   against a recent crossing from a sibling camera; the loser is demoted via a
   `demote` bus message (applied by the writer — single-writer discipline) and a
   `dedup_correction` broadcast.
4. The **LiveBroadcaster** forwards `crossing`, `gap`, `correction` and `drift`
   messages to all `/ws/live` dashboard clients.

## Data model

All tenant-owned tables carry `restaurant_id` (multi-tenant by design; a site process
serves one `active_restaurant_slug`). Key tables:

- `restaurants`, `cameras` (rtsp|usb|file, exit group membership, optional model
  override), `exit_groups` (dedup window/strategy), `lines` (normalized 0..1
  coordinates + per-line tuning), `categories`, `menu_items`,
  `class_mappings`, `models_registry`
- `events` — append-only crossing log: ULID id, UTC ms timestamps, category/menu
  item, direction, confidence, anchor point, `snapshot_path`, `dedup_group_id` +
  `is_canonical` (duplicates are kept, auditable, never deleted)
- `data_gaps` — declared blind spots (outages, pipeline start/stop, drift alarms)
  surfaced in reports
- `counting_eval_runs` — persisted evaluation campaigns
- `sessions` — service shifts/periods

Schema migrations are managed with Alembic from day one (`toskana init-db` =
`alembic upgrade head`).

## Design decisions

- **Testability first**: the `DetectorBackend` protocol lets the entire counting,
  tracking, dedup and API stack run deterministically in CI with the synthetic
  detector — no GPU, no weights, no network.
- **SQLite (WAL) + single writer**: one site's event rate is small; a single writer
  thread removes all write contention and keeps the deployment a single file.
- **MJPEG, not WebRTC**: previews are served same-host over
  `multipart/x-mixed-replace` with overlays rendered server-side — zero client
  dependencies.
- **Normalized line coordinates** survive camera resolution changes.
- **ULIDs + UTC + `restaurant_id` everywhere** keep future multi-site aggregation
  unblocked.
