# Toskana

Toskana is a local-first counting system for restaurant pass-through counters: an AI
vision pipeline (object detection + multi-object tracking) counts every drink and dish
that crosses a virtual line between the kitchen side and the customer side, **per item —
not per person** — with category, direction, camera, and timestamp. Everything runs on a
single on-premise machine (video never leaves the site) and serves a German/English
dashboard in the browser.

## Features

- **Per-item line-crossing counting** — hysteresis, cooldown, coasting through
  occlusions and an ID-switch guard; a tray with 3 items counts as 3 (release-gated).
- **Returns tracked separately** — out / in / net per category, per shift.
- **Multi-camera exits without double counting** — auditable cross-camera dedup with
  manual review/unmerge in the dashboard.
- **Audit snapshot per event** — every count is verifiable with an image; snapshots
  expire per configurable GDPR retention.
- **Live dashboard (de/en)** — MJPEG previews, live counters over WebSocket, event
  feed, statistics with DST-correct aggregation, CSV export, POS reconciliation.
- **Admin in the browser** — cameras (RTSP/USB/file), drag-and-drop line editor with
  direction arrow and hot reload, categories & menu, class mappings, model registry
  with one-click activate/rollback.
- **Health monitoring** — per-camera FPS with startup warnings, declared data gaps in
  reports, SSIM camera-drift watchdog with recalibrate endpoint, DB size.
- **Training pipeline** — YouTube/local ingest → frame extraction → autolabel →
  review → leakage-guarded dataset → fine-tune → gated export → registry, all with
  provenance manifests. Phase 2 counts named menu items with per-restaurant models.
- **Evaluation harness** — `toskana eval-counting` scores precision/recall/MAE per
  category *and per hour* against annotated ground truth; runs are persisted.
- **Deterministic CI** — a synthetic detector backend runs the whole stack (counting,
  dedup, API, eval) without GPU, weights or network.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]          # add .[ml] for real YOLO inference (torch/ultralytics)

cp config.example.yaml config.yaml   # optional; env vars TOSKANA_* override

toskana init-db                # create/upgrade the SQLite schema (alembic)
toskana seed                   # demo restaurant, categories, cameras, lines, mappings
toskana run                    # dashboard at http://127.0.0.1:8420/
```

Try it without cameras: generate a deterministic demo video and count it —

```bash
python -m tests.tools.make_synthetic_video --scenario tray_carry_3 --out-dir data/synthetic
# the video extension depends on the available codec, hence the shell glob:
toskana simulate --video data/synthetic/tray_carry_3/tray_carry_3.*
toskana eval-counting --video data/synthetic/tray_carry_3/tray_carry_3.* \
    --gt data/synthetic/tray_carry_3/ground_truth.json --min-score 1.0
```

CLI: `run` (server), `init-db`, `seed`, `simulate` (count a clip),
`eval-counting` (score against ground truth), `cleanup` (retention pass for cron).

## Dashboard

`/live` camera grid + live counters • `/stats` time series, per-camera bars, out/in/net
donut, shift picker • `/events` filtered event log with snapshots, dedup review, CSV
export • `/reconcile` POS CSV comparison • `/admin/*` restaurants, cameras, line
editor, exit groups, menu & categories, mappings, models • `/system` health, FPS,
drift, DB size.

## Training a restaurant model

```bash
# smoke-proof of the whole chain (CPU, offline, ~2 min):
make smoke-train
```

Real workflow (ingest → frames → autolabel → review → dataset → train → evaluate →
export → activate in the dashboard) is documented step-by-step in
[training/README.md](training/README.md) and [docs/training.md](docs/training.md).

## Development

```bash
make test        # pytest (CI-safe: no GPU, no weights, no network)
make lint        # ruff check + format check
make typecheck   # mypy
make test-web    # vitest + tsc + i18n key parity
make build-web   # production dashboard bundle served by FastAPI
```

## Documentation

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | process model, components, event flow, data model |
| [docs/line-crossing.md](docs/line-crossing.md) | the counting state machine and its guarantees |
| [docs/dedup.md](docs/dedup.md) | cross-camera dedup, strategies, corrections, limits |
| [docs/training.md](docs/training.md) | training pipeline, gates, activation/rollback |
| [docs/deployment.md](docs/deployment.md) | systemd/Windows service, backups, GPU setup, presets, camera placement |
| [docs/privacy.md](docs/privacy.md) | stored data, retention, GDPR/works-council guidance, training-data licensing |
| [docs/testing.md](docs/testing.md) | automated suite + on-site validation checklist |
