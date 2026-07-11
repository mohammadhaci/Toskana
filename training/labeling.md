# Manual labeling workflow

Autolabel gets you 80% of the way; the frames it lists in
`data/labels/review_queue.txt` (low/no confidence) plus a random sample of
the rest need a human pass. Labels are plain YOLO format — one `.txt` per
image, `class_id cx cy w h` normalized to 0..1 — with the class order defined
by `data/labels/classes.txt`.

## Option A — Label Studio (recommended)

1. Install and start (in any venv, does not need the project deps):

   ```bash
   pip install label-studio
   label-studio start
   ```

2. Create a project → **Labeling Setup** → *Object Detection with Bounding
   Boxes*. Add one label per line **in exactly the order of
   `data/labels/classes.txt`** (e.g. `drink`, `main`, `dessert`) — YOLO class
   ids are positional, a different order silently corrupts the dataset.

3. Import the frames to review: upload the images referenced by
   `review_queue.txt` (they live under `data/frames/<video>/...`). To
   pre-load the autolabel boxes for correction instead of starting blank, use
   the Label Studio converter to import YOLO predictions:

   ```bash
   pip install label-studio-converter
   label-studio-converter import yolo -i data/labels --image-root-url "/data/local-files/?d=frames"
   ```

4. Correct boxes: fix class, tighten geometry, add missed items, delete
   phantoms. Rules of thumb:
   - box the item itself (plate/glass), not the hand or the tray;
   - label partially occluded items if ≥ ~50% visible;
   - keep one consistent decision for stacked/nested items and stick to it.

5. Export → **YOLO** format. Unzip and copy the exported `labels/*.txt` back
   over the matching files in `data/labels/<video>/` (keep the per-video
   subdirectories!). Check that the exported `classes.txt` matches yours.

6. Validate the re-import before building a dataset:

   ```bash
   python training/validate_labels.py --labels data/labels/ --images data/frames/
   ```

   Exit code 1 means malformed labels — fix before continuing.

## Option B — CVAT (alternative)

[CVAT](https://github.com/cvat-ai/cvat) suits bigger teams / longer projects
(runs via docker compose). Create a task with the same ordered label set,
upload the frames, annotate, then export as **YOLO 1.1**. CVAT's export
nests `obj_train_data/`; move the `.txt` files back into the matching
`data/labels/<video>/` layout and run `validate_labels.py` as above.

## Rules that keep the dataset sound

- **Never reorder or extend `classes.txt` mid-dataset.** New class → new
  labeling round + new dataset version.
- Keep the per-video directory layout: `build_dataset.py` uses it as the
  train/val leakage guard.
- Empty label files are valid (background frames) and useful — don't delete
  them.
- Re-run `validate_labels.py` after every import; it catches out-of-range
  class ids, non-normalized coordinates and orphan label files.
