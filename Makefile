# Council + COA merged dev workflow (see README "Merged Mode")
.PHONY: setup dev build test clean stop

ROOT := $(abspath .)
WEB := $(ROOT)/web
COA := $(ROOT)/COA/COA_Project
COA_PY := $(COA)/venv/bin/python3
COA_PIP := $(COA)/venv/bin/pip
COA_PY_DOT := $(COA)/.venv/bin/python3
COA_PIP_DOT := $(COA)/.venv/bin/pip

setup:
	python3 -m pip install -r "$(ROOT)/requirements.txt"
	@if [ -x "$(COA_PY)" ]; then \
		"$(COA_PIP)" install -r "$(COA)/requirements.txt"; \
	elif [ -x "$(COA_PY_DOT)" ]; then \
		"$(COA_PIP_DOT)" install -r "$(COA)/requirements.txt"; \
	else \
		echo "WARN: COA venv missing — cd COA/COA_Project && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"; \
	fi
	cd "$(WEB)" && npm install

dev:
	bash "$(ROOT)/scripts/start_merged.sh"

build:
	cd "$(WEB)" && npm run build

test:
	cd "$(ROOT)" && pytest -q

clean:
	find "$(ROOT)" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf "$(WEB)/dist"

stop:
	bash "$(ROOT)/scripts/stop_merged.sh"
