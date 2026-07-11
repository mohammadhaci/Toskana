# Toskana training pipeline

End-to-end chain to produce a restaurant-specific detection model and put it
live in the dashboard. Every stage is a standalone script (`--help` works on
each) that writes a `manifest.json` into its output directory recording
inputs, parameters, source URLs + licenses, and the git SHA — so every
deployed model has an auditable provenance chain back to its raw footage.

```
YouTube / local clips
   │  1. ingest_youtube.py / ingest_local.py      data/raw/       + manifest
   ▼
frames
   │  2. extract_frames.py (interval + phash dedup) data/frames/  + manifest
   ▼
auto labels + review queue
   │  3. autolabel.py (--backend yolo|synthetic)   data/labels/   + manifest
   │  4. manual pass on review_queue.txt           see labeling.md
   │     validate_labels.py after re-import
   ▼
YOLO dataset (train/val, leakage-guarded split)
   │  5. build_dataset.py                          data/datasets/NAME/
   ▼
trained weights
   │  6. train.py (ultralytics)                    runs/NAME/weights/best.pt
   │  7. evaluate.py → metrics.json (export gate)
   ▼
model registry
   │  8. export_model.py → models/{slug}/{name}-{ver}/ + DB rows
   ▼
   9. activate in the dashboard (Models page) — instant rollback the same way
```

All commands below assume the project venv: `source .venv/bin/activate`
(or prefix with `.venv/bin/`).

## 1. Ingest

From YouTube (**prototyping only** — see Licensing below):

```bash
python training/ingest_youtube.py --urls-file urls.txt --out data/raw/
# or: --url https://www.youtube.com/watch?v=... (repeatable)
```

Downloads are capped at 720p. Each video's source URL, title and license are
recorded in `data/raw/manifest.json`; when YouTube does not expose a license
it is recorded as `unknown — verify before production use`. Network/proxy
failures exit with code 2 and actionable guidance on stderr.

From local footage (the production path, and the offline/CI path):

```bash
python training/ingest_local.py --videos shift1.mp4 shift2.mp4 --out data/raw/
```

Both scripts merge into the same `data/raw/manifest.json`, so mixed sources
are fine (each entry keeps its own license field; local files get `local`).

## 2. Extract frames

```bash
python training/extract_frames.py --raw data/raw/ --out data/frames/ \
    --interval-s 1.0 --phash-threshold 8
```

Samples one frame per interval and drops near-duplicates via perceptual-hash
(phash) Hamming distance (`--phash-threshold -1` disables dedup). Frames land
in one subdirectory per source video — this grouping is what the dataset
builder later uses to prevent train/val leakage, so don't reshuffle it.

## 3. Autolabel

```bash
# with a pretrained model (needs weights locally or network to download):
python training/autolabel.py --frames data/frames/ --out data/labels/ \
    --backend yolo --model yolov8n.pt --classes cup,bottle,bowl,pizza,cake \
    --conf 0.35 --review-conf 0.5

# offline/CI (synthetic scenario clips only):
python training/autolabel.py --frames data/frames/ --out data/labels/ --backend synthetic
```

Writes one YOLO `.txt` per image, `classes.txt` (label id order), and
`review_queue.txt` — every image whose best detection confidence is below
`--review-conf` (or that has no detections). Do a manual pass over the queue
before building a serious dataset: see [`labeling.md`](labeling.md).

## 4. Manual labeling & validation

Follow [`labeling.md`](labeling.md) (Label Studio, or CVAT as an
alternative). After exporting corrected labels back into `data/labels/`:

```bash
python training/validate_labels.py --labels data/labels/ --images data/frames/
```

Exits 1 on any format error (bad field counts, class ids out of range,
coordinates outside 0..1, orphan label files).

## 5. Build the dataset

```bash
python training/build_dataset.py --frames data/frames/ --labels data/labels/ \
    --out data/datasets/toskana-v1/ --classes drink,main,dessert --val-ratio 0.2
```

Produces the standard YOLO layout (`images/{train,val}`, `labels/{train,val}`,
`data.yaml`) and prints a per-class instance report (also in the manifest).
**Leakage guard:** all frames of one source video always go to the same
split. Record at least two clips; with a single clip there is no held-out
val set (the script warns loudly and points val at train).

## 6. Train

### Real training (GPU machine)

On the training machine, install the ML extras with a CUDA build of torch:

```bash
pip install -e .[ml,train]      # check https://pytorch.org for the right CUDA wheel
python training/train.py --data data/datasets/toskana-v1/data.yaml \
    --base yolov8n.pt --epochs 100 --imgsz 640 --device 0 --out runs/toskana-v1
```

Guidance:

- `--base yolov8n.pt` (fine-tune from pretrained COCO weights) is what you
  want for real models; `yolov8s.pt` if the GPU allows and CPU inference
  budget at the restaurant permits.
- Expected times for ~2–5k images at imgsz 640, 100 epochs: yolov8n roughly
  1–2 h on an RTX 3060-class GPU, yolov8s roughly 2–4 h. CPU training of real
  models is impractical — use it only for the smoke run below.
- The run directory (`runs/toskana-v1/`) gets a manifest embedding the
  dataset manifest chain.

### Smoke training (CPU, offline — proves the pipeline, not accuracy)

`--base yolov8n.yaml` builds the architecture from scratch (random init), so
no pretrained weights need to be downloaded:

```bash
python training/train.py --data data/datasets/smoke/data.yaml \
    --base yolov8n.yaml --epochs 2 --imgsz 160 --out runs/smoke
```

`make smoke-train` runs the entire chain (synthetic clips → ingest → frames →
autolabel `--backend synthetic` → dataset → 2-epoch train → evaluate →
export) on CPU in a few minutes. mAP will be near-random — that's expected.

## 7. Evaluate (export gate)

```bash
python training/evaluate.py --weights runs/toskana-v1/weights/best.pt \
    --data data/datasets/toskana-v1/data.yaml --min-map50 0.6
```

Writes `metrics.json` (mAP50, mAP50-95, per-class) and **exits 1 if mAP50 is
below `--min-map50`** — a failing gate means the model must not be exported.
Project policy: Phase 2 (named menu items) requires mAP50 ≥ 0.6 per class
before activation.

## 8. Export into the registry

```bash
python training/export_model.py --weights runs/toskana-v1/weights/best.pt \
    --restaurant toskana --name toskana-phase1 --version 1 \
    --db ./toskana.db --metrics runs/toskana-v1/weights/metrics.json \
    --dataset data/datasets/toskana-v1 \
    --map-category drink=drink --map-category main=main --map-category dessert=dessert
```

This copies the weights to `models/toskana/toskana-phase1-1/best.pt`, writes
`meta.json` (classes, metrics, and the embedded dataset→frames→ingest
manifest chain with all source licenses), inserts a `models_registry` row
(kind `finetuned`, **inactive**), and creates `class_mappings` rows for every
class whose category is known (`--map-category`, or automatically when the
class name equals a category key). Remaining classes are listed for the admin
to map in the dashboard. Re-running with the same name+version is idempotent.

## 9. Activate / rollback (dashboard)

Open the dashboard → **Admin → Models**. The exported model appears inactive:

1. Complete any missing class mappings under **Admin → Mappings**.
2. Click **Activate** — the restaurant's camera pipelines reload with the new
   weights and mapping set atomically (`POST /api/restaurants/{id}/models/{model_id}/activate`).
3. **Rollback:** activate the previous model the same way — activation always
   deactivates every other model visible to the restaurant, so rolling back
   is one click and takes effect immediately.

## Licensing policy

- **YouTube footage is for prototyping only.** It lets you exercise the
  pipeline and get a feel for label quality, nothing more. Every downloaded
  video's URL and license are recorded in the ingest manifest; most YouTube
  videos are under the Standard YouTube License, which does **not** permit
  training production models on them.
- **Production models are trained exclusively on the restaurant's own
  footage** recorded with documented consent (works council / DPIA notes —
  see `docs/privacy.md`). `ingest_local.py` records these with license
  `local`.
- The manifest chain (ingest → frames → labels → dataset → train → export)
  is embedded in the exported model's `meta.json`, so for any deployed model
  you can prove which sources — and which licenses — went into it. Do not
  activate a model whose chain contains `unknown — verify before production
  use` sources.
