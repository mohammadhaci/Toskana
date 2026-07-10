# Connecting Restaurant Cameras

Toskana counts items from any camera it can read. Three source types are
supported per camera (Admin → Cameras):

| Type | Source field | Typical use |
|---|---|---|
| `rtsp` | RTSP URL (see below) | IP cameras / NVR & DVR recorders — the normal restaurant setup |
| `usb` | Device index: `0`, `1`, … | A webcam plugged into the Toskana machine |
| `file` | Path to a video file | Testing, demos, analyzing recordings |

## IP cameras (RTSP)

Requirements:
- Camera and the Toskana machine on the **same network** (or routable).
- Give the camera a **static IP** (or a DHCP reservation in your router).
- Know the camera's username/password (set at first camera setup — do not
  keep factory defaults).
- RTSP enabled in the camera's own settings (usually on by default,
  port **554**).

The dashboard's *Add Camera* wizard builds the URL for you from vendor
presets. The equivalent URL patterns, for reference:

| Vendor | Main stream URL pattern |
|---|---|
| Hikvision (+ many rebrands) | `rtsp://USER:PASS@IP:554/Streaming/Channels/101` (substream: `102`) |
| Dahua (+ Amcrest, Lorex) | `rtsp://USER:PASS@IP:554/cam/realmonitor?channel=1&subtype=0` |
| Reolink | `rtsp://USER:PASS@IP:554/h264Preview_01_main` |
| TP-Link Tapo/Vigi | `rtsp://USER:PASS@IP:554/stream1` (sub: `stream2`) |
| Uniview (UNV) | `rtsp://USER:PASS@IP:554/media/video1` |
| Axis | `rtsp://USER:PASS@IP:554/axis-media/media.amp` |
| Ubiquiti UniFi Protect | enable the RTSPS/RTSP share per camera in Protect and copy its URL |
| Generic ONVIF camera | check the manual; often `rtsp://USER:PASS@IP:554/onvif1` |

**Cameras behind an NVR/DVR:** point Toskana at the recorder, not the
camera. Hikvision NVR channel N: `.../Channels/N01` (e.g. channel 3 →
`301`). Dahua NVR: `...realmonitor?channel=N&subtype=0`.

Notes:
- Special characters in passwords must be URL-encoded (`@` → `%40`).
- Prefer the **substream** (lower resolution) on CPU-only machines — the
  detector runs at 480–640 px anyway; a 4K main stream only wastes CPU.
- If the image stalls or disconnects, Toskana reconnects automatically
  with backoff and records the outage as a data gap (visible in reports).

## Placement guidance for accurate counting

- Mount the camera **high, looking down** at the pass/exit so items on a
  tray appear separated, not stacked behind each other.
- The virtual line should cross the **full width** of the physical path;
  items must be visible on both sides of it for a few frames.
- Avoid heat-lamp glare directly into the lens and keep the dome/lens
  clean — kitchen steam films it over within days (add it to the cleaning
  rota). The drift alarm (System page) warns when the view changed.
- Two cameras on one exit: put them in the same **Exit Group** so
  crossings seen by both are counted once.

## Quick test without a real camera

`toskana seed` generates two demo clips under `./data/videos/` wired to
the demo restaurant's cameras — `toskana run` counts them immediately.
Any recording of your own pass can be analyzed via the dashboard's
*Analyse* page (upload or URL) or by adding a `file` camera.
