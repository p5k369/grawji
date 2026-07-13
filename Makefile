# grawji convenience targets.
# (see the system packages in the README); everything else is set up here.
#
#   make install   one-command venv + rawji (from git) + grawji
#   make run       launch the app
#   make dev       same as install plus dev tools and pre-commit hooks
#   make flatpak   build and install the Flatpak (needs flatpak-builder)

PYTHON ?= python3
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
RAWJI ?=
APP_ID := io.github.p5k369.grawji
MANIFEST := flatpak/$(APP_ID).yaml

.PHONY: install dev run test lint format clean flatpak flatpak-bundle

$(VENV):
	$(PYTHON) -m venv --system-site-packages $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)
	$(if $(RAWJI),$(PIP) install $(RAWJI))
	$(PIP) install -e .
	@echo "Done. Put the camera in USB RAW CONV. mode, then: make run"

dev: $(VENV)
	$(if $(RAWJI),$(PIP) install $(RAWJI))
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

flatpak:
	flatpak-builder --user --install --force-clean build-flatpak $(MANIFEST)
	@echo "Done. Run: flatpak run $(APP_ID)"

flatpak-bundle:
	flatpak-builder --user --force-clean --repo=build-flatpak-repo \
		build-flatpak $(MANIFEST)
	flatpak build-bundle build-flatpak-repo grawji.flatpak $(APP_ID)
	@echo "Wrote grawji.flatpak"

clean:
	rm -rf $(VENV) build-flatpak build-flatpak-repo .flatpak-builder
