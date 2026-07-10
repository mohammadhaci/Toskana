PYTHON ?= python
PIP ?= pip

.PHONY: dev test lint typecheck build-web test-web synthetic smoke-train

dev:
	$(PIP) install -e .[dev]

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy

test-web:
	cd web && npm run check && npm test

# --- later milestones ---

build-web:
	cd web && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund) && npm run build

synthetic:
	$(PYTHON) -m tests.tools.make_synthetic_video --scenario all --out-dir data/synthetic

# Full CI-able training chain on generated synthetic clips (CPU, offline,
# no pretrained weights). Proves the pipeline, not accuracy (~random mAP).
SMOKE_DIR ?= data/smoke

smoke-train:
	rm -rf $(SMOKE_DIR)
	$(PYTHON) -m tests.tools.make_synthetic_video --scenario single_drink --out-dir $(SMOKE_DIR)/clips
	$(PYTHON) -m tests.tools.make_synthetic_video --scenario tray_carry_3 --out-dir $(SMOKE_DIR)/clips
	$(PYTHON) training/ingest_local.py \
		--videos $(SMOKE_DIR)/clips/single_drink/single_drink.* \
		         $(SMOKE_DIR)/clips/tray_carry_3/tray_carry_3.* \
		--out $(SMOKE_DIR)/raw
	$(PYTHON) training/extract_frames.py --raw $(SMOKE_DIR)/raw --out $(SMOKE_DIR)/frames \
		--interval-s 0.2 --phash-threshold -1
	$(PYTHON) training/autolabel.py --frames $(SMOKE_DIR)/frames --out $(SMOKE_DIR)/labels \
		--backend synthetic
	$(PYTHON) training/validate_labels.py --labels $(SMOKE_DIR)/labels --images $(SMOKE_DIR)/frames
	$(PYTHON) training/build_dataset.py --frames $(SMOKE_DIR)/frames --labels $(SMOKE_DIR)/labels \
		--out $(SMOKE_DIR)/dataset --classes drink,main,dessert --val-ratio 0.3
	$(PYTHON) training/train.py --data $(SMOKE_DIR)/dataset/data.yaml \
		--base yolov8n.yaml --epochs 2 --imgsz 160 --device cpu --out $(SMOKE_DIR)/runs/smoke
	$(PYTHON) training/evaluate.py --weights $(SMOKE_DIR)/runs/smoke/weights/best.pt \
		--data $(SMOKE_DIR)/dataset/data.yaml --imgsz 160 --device cpu \
		--out $(SMOKE_DIR)/runs/smoke/metrics.json
	TOSKANA_DB_PATH=$(SMOKE_DIR)/smoke.db $(PYTHON) -m toskana.cli init-db
	TOSKANA_DB_PATH=$(SMOKE_DIR)/smoke.db $(PYTHON) -m toskana.cli seed
	$(PYTHON) training/export_model.py --weights $(SMOKE_DIR)/runs/smoke/weights/best.pt \
		--restaurant toskana --name smoke --version 1 --db $(SMOKE_DIR)/smoke.db \
		--metrics $(SMOKE_DIR)/runs/smoke/metrics.json --dataset $(SMOKE_DIR)/dataset \
		--models-dir $(SMOKE_DIR)/models
	@echo "smoke-train OK — exported $(SMOKE_DIR)/models/toskana/smoke-1/best.pt"
