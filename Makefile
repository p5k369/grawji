# grawji convenience targets.
# (see the system packages in the README); everything else is set up here.
#
#   make install   one-command venv + rawji (from git) + grawji
#   make run       launch the app
#   make dev       same as install plus dev tools and pre-commit hooks

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
RAWJI ?= rawji@git+https://github.com/pinpox/rawji

.PHONY: install dev run test lint format clean

$(VENV):
	$(PYTHON) -m venv --system-site-packages $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)
	$(PIP) install $(RAWJI)
	$(PIP) install -e .
	@echo "Done. Put the camera in USB RAW CONV. mode, then: make run"

dev: $(VENV)
	$(PIP) install $(RAWJI)
	$(PIP) install -e ".[dev]"
	$(VENV)/bin/pre-commit install
	@echo "Dev environment ready."

run:
	$(PY) -m grawji

test:
	$(PY) -m pytest

lint:
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/ruff format --check src tests
	$(PY) -m mypy src tests

format:
	$(VENV)/bin/ruff format src tests

clean:
	rm -rf $(VENV)
