# Training pipeline

Phase 1 counts **generic categories** (drink, main, dessert …) with a pretrained
model + class mappings. Phase 2 fine-tunes a per-restaurant model that recognizes
**named menu items**. The pipeline that produces such a model lives in `training/`
— every stage is a standalone script writing a `manifest.json` (inputs, parameters,
source licenses, git SHA) so every deployed model has auditable provenance.

Full command-by-command instructions: [`training/README.md`](../training/README.md).
Manual annotation workflow (Label Studio / CVAT): [`training/labeling.md`](../training/labeling.md).

## Stages

| # | Script | Output |
|---|---|---|
| 1 | `ingest_youtube.py` / `ingest_local.py` | `data/raw/` + manifest (yt-dlp path fails gracefully offline) |
| 2 | `extract_frames.py` | frames (interval / scene-change sampling, phash near-duplicate drop) |
| 3 | `autolabel.py` | YOLO labels from a pretrained model + `review_queue.txt` for low-confidence frames |
| 4 | manual review | corrected labels; `validate_labels.py` checks format on re-import |
| 5 | `build_dataset.py` | train/val split, **leakage-guarded**: frames of one video never span both splits |
| 6 | `train.py` | Ultralytics fine-tune (`--epochs 2 --imgsz 320` smoke on CPU; real runs on GPU) |
| 7 | `evaluate.py` | `metrics.json`; the export **gate** refuses models below the mAP50 threshold |
| 8 | `export_model.py` | `models/{restaurant}/{name}-{ver}/best.pt` + registry row + class-mapping skeleton |
| 9 | dashboard → Models | activate (pipelines restart with the new weights) / instant rollback |

## Activation and fallback behavior

- Activating a model is atomic: the registry row flips, camera pipelines hot-reload,
  and the previous model remains registered for one-click rollback.
- A class the model reports but no mapping covers is stored with `category NULL` —
  visible in the Events page, never silently dropped.
- **Phase 2 fallback chain**: a menu item maps to its category as well, so a brand
  new dish on the menu counts inside its generic category immediately, before any
  retraining.

## Quality gates

- CI runs stages 1–5 on bundled synthetic clips every PR (dataset integrity, split
  leakage, label format).
- `make smoke-train` proves the whole chain (2 epochs, CPU, offline) through to a
  registered, activatable model.
- **Phase 2 activation gate**: per-item mAP50 ≥ 0.6 on the held-out validation set
  before a model may be activated for named-item counting.

## Data policy (summary — see [privacy.md](privacy.md))

- YouTube-sourced footage is for **prototyping only**; manifests record every source
  URL and license.
- Production models are trained on the restaurant's **own** footage with documented
  consent; the training data never leaves the site.
