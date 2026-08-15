.PHONY: bootstrap lint test test-targeted build verify clean

VENV ?= .venv
PYTHON := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff

bootstrap:
	SIDEPULSE_VENV="$(abspath $(VENV))" ./scripts/bootstrap-dev.sh

lint: bootstrap
	$(RUFF) check src tests

test: bootstrap
	$(PYTHON) -m pytest tests/ -q

test-targeted: bootstrap
	$(PYTHON) -m pytest tests/test_device_projection.py tests/test_packaging_contract.py -q

build: bootstrap
	$(PYTHON) -m pip install --quiet build twine
	rm -rf build dist
	$(PYTHON) -m build
	$(PYTHON) -m twine check dist/*

verify:
	SIDEPULSE_VENV="$(abspath $(VENV))" ./scripts/verify.sh

clean:
	rm -rf .pytest_cache .ruff_cache build dist htmlcov
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
