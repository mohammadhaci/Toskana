# Toskana

Toskana is a local-first counting system for restaurant pass-through counters: an AI
vision pipeline (object detection + multi-object tracking) counts every drink and dish
that crosses a virtual line between the kitchen side and the customer side, per item —
not per person — with category, direction, camera, and timestamp. Everything runs on a
single on-premise machine (video never leaves the site) and serves a German/English
dashboard in the browser.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]          # add .[ml] for real YOLO inference (torch/ultralytics)

cp config.example.yaml config.yaml   # optional; env vars TOSKANA_* override

toskana init-db                # create/upgrade the SQLite schema (alembic)
toskana seed                   # demo restaurant, categories, cameras, lines, mappings
toskana run                    # dashboard server (stub until M4)
```

Development:

```bash
make test        # pytest (CI-safe: no GPU, no weights, no network)
make lint        # ruff check + format check
make typecheck   # mypy
```

## Architecture

```
toskana run --config config.yaml
  └── FastAPI (uvicorn) :8420 → serves web/dist (dashboard) + REST + WebSocket + MJPEG
  └── PipelineManager (threads)
        └── per camera: Capture → Detector+Tracker → LineCrossingCounter
        └── DedupEngine (per exit group) → EventWriter (single DB writer, SQLite WAL)
```

- **Multi-tenant schema** (`src/toskana/db/models.py`): every tenant-owned table carries
  `restaurant_id`; events use ULID primary keys and UTC millisecond timestamps.
- **Counting lines** are stored with normalized 0..1 coordinates plus per-line tuning
  (min track age, hysteresis, cooldown) so they survive resolution changes.
- **Multiple cameras per exit** are grouped in `exit_groups`; duplicate events across
  cameras are kept (auditable) and one per dedup group is marked canonical.
- **Pluggable detection**: a `DetectorBackend` protocol (M2) lets CI run a deterministic
  synthetic shape detector while production uses Ultralytics YOLO + ByteTrack.
- **Phase 1** counts generic categories via `class_mappings` (model class → category);
  **Phase 2** fine-tuned per-restaurant models map classes to named `menu_items`.

Milestones: M0 skeleton/DB/CI (this) → M1 video ingest + synthetic scenarios → M2
detector/tracker abstraction → M3 line-crossing engine → M4 API → M5+ dashboard,
dedup, training pipeline, evaluation harness.
