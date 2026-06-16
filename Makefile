# ─── PLINY COMMAND ──────────────────────────────────────────────────────────
# Convenience targets. All idempotent.
#
#   make            → install + run (full quickstart)
#   make install    → install dependencies, seed configs, write .env
#   make run        → source .env and launch server.py
#   make stop       → kill any server on :8888
#   make restart    → stop + run
#   make clean      → wipe state/, sessions/, logs/ (NOT rt_library)
#   make nuke       → wipe everything including rt_library/ (CAREFUL)

PY ?= python3
PORT ?= 8888

.PHONY: default help install run stop restart clean nuke status smoke-test version

default: quickstart

help:
	@echo "Pliny Command — make targets:"
	@echo "  make              one-command install + launch (alias: quickstart)"
	@echo "  make install      install deps, seed gauntlet configs, write .env"
	@echo "  make run          launch server with .env loaded"
	@echo "  make stop         kill any server on :$(PORT)"
	@echo "  make restart      stop + run"
	@echo "  make status       show server pid + url"
	@echo "  make version      print VERSION + resolved PLINY_HOME / PLINY_WORKSHOP"
	@echo "  make smoke-test   verify deps + server imports cleanly (no launch)"
	@echo "  make clean        wipe runtime state (sessions/logs/state)"
	@echo "  make nuke         wipe EVERYTHING including rt_library/ (careful)"

version:
	@if [ -f VERSION ]; then \
	  printf "Pliny Command v%s\n" "$$(cat VERSION)"; \
	else \
	  echo "Pliny Command (no VERSION file — dev build)"; \
	fi
	@if [ -f .env ]; then set -a && . ./.env && set +a; fi; \
	HOME_DIR="$${PLINY_HOME:-$$(pwd)}"; \
	WS_DIR="$${PLINY_WORKSHOP:-$$HOME/pliny-workshop}"; \
	printf "  PLINY_HOME     = %s\n" "$$HOME_DIR"; \
	printf "  PLINY_WORKSHOP = %s\n" "$$WS_DIR"

smoke-test:
	@echo "▸ smoke-test: dependency check"
	@$(PY) -c 'import sys; assert sys.version_info >= (3, 9), "need Python ≥3.9"' \
	  && echo "  ✓ Python ≥3.9"
	@$(PY) -c 'import yaml' 2>/dev/null \
	  && echo "  ✓ pyyaml importable" \
	  || (echo "  ✗ pyyaml missing — run: make install" && exit 1)
	@echo "▸ smoke-test: server.py imports cleanly"
	@PLINY_WORKSHOP="$$(mktemp -d)/pliny-workshop-smoke" \
	  $(PY) -c 'import importlib.util, pathlib; \
	    spec = importlib.util.spec_from_file_location("server", pathlib.Path("server.py")); \
	    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); \
	    print("  ✓ server.py imports — version=" + getattr(mod, "PLINY_VERSION", "dev"))'
	@echo "▸ smoke-test: HTML template tokens present"
	@for f in index.html redteam.html arcade.html; do \
	  if grep -q '/Users/ep' "$$f"; then \
	    echo "  ✗ $$f still contains /Users/ep paths"; exit 1; \
	  fi; \
	done
	@echo "  ✓ no hardcoded /Users/ep in HTML"
	@echo "▸ smoke-test: PASSED — safe to launch with 'make run'"

quickstart:
	@./quickstart.sh

install:
	@./install.sh

run:
	@if [ -f .env ]; then set -a && . ./.env && set +a; fi; \
	echo "🐉 Pliny Command starting on http://localhost:$(PORT)"; \
	$(PY) server.py

stop:
	@pids=$$(lsof -ti :$(PORT) 2>/dev/null || true); \
	if [ -n "$$pids" ]; then \
	  echo "stopping pid(s): $$pids"; \
	  kill -9 $$pids 2>/dev/null || true; \
	  echo "✓ stopped"; \
	else \
	  echo "no server on :$(PORT)"; \
	fi

restart: stop run

status:
	@pids=$$(lsof -ti :$(PORT) 2>/dev/null || true); \
	if [ -n "$$pids" ]; then \
	  echo "🐉 server running — pid $$pids — http://localhost:$(PORT)"; \
	else \
	  echo "💤 no server on :$(PORT)"; \
	fi

clean:
	@echo "wiping runtime state (sessions/, logs/, state/)…"
	@rm -rf sessions/* logs/* state/* 2>/dev/null || true
	@echo "✓ runtime state cleared (rt_library preserved)"

nuke:
	@echo "⚠  This wipes rt_library/ — including all gauntlet runs and trophies."
	@printf "Type 'YES' to confirm: "; read confirm; \
	if [ "$$confirm" = "YES" ]; then \
	  rm -rf sessions/* logs/* state/* rt_library/* 2>/dev/null || true; \
	  ./install.sh --quiet; \
	  echo "✓ nuked + reseeded"; \
	else \
	  echo "aborted"; \
	fi
