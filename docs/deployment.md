# Deployment

One machine per restaurant. Video never leaves the site; the dashboard is served on
the local network.

## Install

```bash
python3.11 -m venv /opt/toskana/venv
/opt/toskana/venv/bin/pip install -e "/opt/toskana/app[ml]"   # ml = torch + ultralytics
cd /opt/toskana/app
cp config.example.yaml config.yaml       # edit db_path, snapshots_dir, cameras via dashboard
/opt/toskana/venv/bin/toskana init-db
/opt/toskana/venv/bin/toskana seed       # first run only: demo restaurant + categories
```

Set `detector_backend: yolo` in `config.yaml` for production (the default
`synthetic` backend is for CI/demo).

## Security

By default the dashboard/API is **open** — intended for a trusted LAN where the
counter machine sits next to the POS. Before exposing it beyond that (guest
Wi-Fi, VPN, port-forward, reverse proxy), set an API token:

```yaml
api_token: a-long-random-string        # or: TOSKANA_API_TOKEN=... in the unit file
```

With a token set, every `/api` route and `/ws/live` return 401 without
`Authorization: Bearer <token>`; the MJPEG stream, snapshots, CSV export and the
WebSocket also accept `?token=<token>` (browsers cannot set headers on `<img>`
tags). Only `GET /api/system/health` stays open for monitoring probes. The
dashboard prompts for the token on the first 401 and stores it in the browser's
localStorage.

The token protects the API, not the transport — for anything beyond the LAN put
a TLS reverse proxy (Caddy, nginx) or VPN in front. Optionally set
`alert_webhook_url` to receive `camera_down` / `drift` / `camera_recovered`
JSON POSTs at an internal endpoint (5 s timeout, fire-and-forget).

## Linux service (systemd)

`/etc/systemd/system/toskana.service`:

```ini
[Unit]
Description=Toskana pass-through counter
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=toskana
WorkingDirectory=/opt/toskana/app
Environment=TOSKANA_CONFIG=/opt/toskana/app/config.yaml
ExecStart=/opt/toskana/venv/bin/toskana run --host 0.0.0.0
Restart=always
RestartSec=5
# Hardening (optional but recommended)
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=/opt/toskana/app

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now toskana
journalctl -u toskana -f        # logs
```

## Windows service

Use [NSSM](https://nssm.cc) (or `sc.exe`) to wrap the CLI:

```powershell
nssm install Toskana C:\toskana\venv\Scripts\toskana.exe run --host 0.0.0.0
nssm set Toskana AppDirectory C:\toskana\app
nssm set Toskana AppEnvironmentExtra TOSKANA_CONFIG=C:\toskana\app\config.yaml
nssm start Toskana
```

Alternatively schedule `toskana run` at boot via Task Scheduler ("Run whether user
is logged on or not"). Install torch per the GPU section below before enabling the
YOLO backend.

## SQLite backup

The database runs in WAL mode — **never copy the `.db` file while the service
writes**. Use the online backup API instead (safe while running):

```bash
sqlite3 /opt/toskana/app/toskana.db ".backup '/backups/toskana-$(date +%F).db'"
# or: sqlite3 toskana.db "VACUUM INTO '/backups/toskana-YYYY-MM-DD.db'"
```

Schedule daily (cron / Task Scheduler) and rotate. Back up `models/` after each
training export; event snapshots (`snapshots_dir`) are expendable audit data with a
retention limit — back them up only if your privacy policy allows it.

## GPU setup (optional, recommended for > 2 cameras)

```bash
# CUDA build of torch — pick the index URL matching your driver's CUDA version:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install ultralytics
```

Verify: `python -c "import torch; print(torch.cuda.is_available())"` — the System
page also shows `cuda_available`. With `device: auto` the pipeline picks
cuda → mps → cpu automatically.

## Performance presets

`performance_preset` in `config.yaml` (`auto` resolves by CUDA availability):

| Preset | imgsz | Frames processed | Intended hardware |
|---|---|---|---|
| `cpu` | 480 | every 2nd frame | modern 4+ core desktop CPU |
| `gpu` | 640 | every frame | any CUDA GPU (≥ 4 GB VRAM) |
| `auto` (default) | — | — | `gpu` when CUDA is available, else `cpu` |

The manager measures FPS ~5 s after each camera starts and **warns below 8 FPS**
(counting accuracy degrades). Remedies: `cpu` preset, lower camera resolution/FPS,
fewer cameras per machine, or a GPU. Guideline: a GPU is advisable from ~3 cameras
upward; a recent 6-core CPU handles 1–2 cameras at 15 FPS.

## Camera placement

- **High, steep angle** (45–70° downward) over the pass — items on a tray must
  appear as *separate* objects, not one blob. Avoid near-horizontal views.
- The counting line should span the full pass with **clear space on both sides**;
  place it where items move perpendicular to it, not along it.
- Fixed mounting only — no autofocus hunting, no auto-repositioning. After any
  physical adjustment, re-calibrate the drift reference
  (`POST /api/cameras/{id}/calibrate` or the dashboard button); the SSIM watchdog
  alarms when the view changes and flags the period's data as suspect.
- Constant lighting matters more than resolution; 1080p at a stable 15 FPS beats 4K
  at 7 FPS. Avoid direct backlight/glare on the counter.
- For two cameras on one exit, put them in one **exit group** (see
  [dedup.md](dedup.md)) and mark the better view as primary.

## Retention & housekeeping

- Event snapshots are deleted after `snapshot_retention_days` (default 30) by a
  daily in-process job; trigger manually with `POST /api/system/retention/run` or
  `toskana cleanup` (cron-friendly). See [privacy.md](privacy.md).
- The System page shows DB size, per-camera FPS, gaps, drift status and dedup
  statistics, with per-camera **Calibrate** buttons (drift reference) and a
  **Run retention cleanup** button.

## Contract/commercial checklist (templates, not code)

SLA & support hours, hardware ownership and replacement, data ownership (the
restaurant owns its DB, snapshots and trained models), works-council agreement
where applicable ([privacy.md](privacy.md)), and the POS reconciliation cadence
belong in the operating agreement per site.
