PYTHON ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)
PYTEST ?= $(PYTHON) -m pytest
CONFIG ?= configs/rig_sim.yaml

.PHONY: help install native image vcan test cov lint format typecheck soak report dash clean all

help:
	@echo "targets: install native image vcan test cov lint format typecheck soak report dash clean"

install:
	$(PYTHON) -m pip install -e ".[dev]"

native:
	$(MAKE) -C native

image:
	$(PYTHON) scripts/make_image.py configs/rootfs.img

vcan:
	bash scripts/setup_vcan.sh

test:
	$(PYTEST) -m "hil and sim" -q

cov:
	$(PYTEST) -m "hil and sim" --cov=hilharness --cov-report=term-missing --cov-fail-under=80

lint:
	$(PYTHON) -m ruff check hilharness simtarget tests scripts
	$(PYTHON) -m black --check hilharness simtarget tests scripts
	$(PYTHON) -m mypy hilharness simtarget

format:
	$(PYTHON) -m ruff check --fix hilharness simtarget tests scripts
	$(PYTHON) -m black hilharness simtarget tests scripts

typecheck:
	$(PYTHON) -m mypy hilharness simtarget

soak:
	$(PYTHON) -m hilharness.cli soak --cycles $${CYCLES:-50} --config $(CONFIG)

report:
	$(PYTHON) -m hilharness.cli report --config $(CONFIG)

dash:
	docker compose up -d

clean:
	$(MAKE) -C native clean || true
	rm -rf results/*.db results/dut_root results/transcripts results/can_logs
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov

all: native lint test
