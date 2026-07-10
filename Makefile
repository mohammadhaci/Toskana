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
	@echo "TODO (M1): python tests/tools/make_synthetic_video.py --all"

smoke-train:
	@echo "TODO (M9): training smoke run (2 epochs, imgsz 320, CPU)"
