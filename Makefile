PYTHON ?= python
PIP ?= pip

.PHONY: dev test lint typecheck build-web synthetic smoke-train

dev:
	$(PIP) install -e .[dev]

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy

# --- later milestones ---

build-web:
	@echo "TODO (M5): cd web && npm ci && npm run build"

synthetic:
	@echo "TODO (M1): python tests/tools/make_synthetic_video.py --all"

smoke-train:
	@echo "TODO (M9): training smoke run (2 epochs, imgsz 320, CPU)"
