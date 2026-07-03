.DEFAULT_GOAL := help

CLI := uv run python -m src.cli

# make ingest DEBUG=1 ...   → adds --debug-mode before the subcommand
ifeq ($(DEBUG),1)
CLI_FLAGS := --debug-mode
else
CLI_FLAGS :=
endif

# make ingest LANG_CODE=de ...  → optional, auto-detected by the CLI if omitted
# (named LANG_CODE, not LANG, because LANG is your shell's locale env var —
# Make auto-imports environment variables, so reusing LANG would silently
# leak your locale, e.g. "C.UTF-8", in as the --lang value)
ifdef LANG_CODE
LANG_FLAG := --lang $(LANG_CODE)
else
LANG_FLAG :=
endif

.PHONY: help install setup-db ingest test test-unit test-integration \
        dbt-seed dbt-run dbt-run-staging dbt-test dbt-full-refresh \
        run clean

help: ## Show this help
	@echo "FitMon — available targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make setup-db"
	@echo "  make ingest FILE=path/to/export.csv SOURCE=strong LANG_CODE=de"
	@echo "  make ingest FILE=path/to/export.csv SOURCE=strong DEBUG=1"
	@echo "  make run ARGS=\"ingest-csv --file path/to/export.csv --source strong --help\""

install: ## Install all dependencies from pyproject.toml
	uv sync

setup-db: ## Create all schemas and tables in Supabase
	$(CLI) $(CLI_FLAGS) setup-db

ingest: ## Ingest a workout CSV — usage: make ingest FILE=... SOURCE=... [LANG_CODE=...] [DEBUG=1]
	@test -n "$(FILE)" || (echo "FILE is required — e.g. make ingest FILE=path/to/export.csv SOURCE=strong" && exit 1)
	@test -n "$(SOURCE)" || (echo "SOURCE is required — e.g. make ingest FILE=path/to/export.csv SOURCE=strong" && exit 1)
	$(CLI) $(CLI_FLAGS) ingest-csv --file $(FILE) --source $(SOURCE) $(LANG_FLAG)

test: test-unit ## Alias for test-unit

test-unit: ## Run unit tests (no DB connection required)
	uv run pytest tests/unit

test-integration: ## Run integration tests
	uv run pytest tests/integration

dbt-seed: ## Load dim_exercises.csv
	cd dbt && uv run dbt seed

dbt-run: ## Run all dbt models
	cd dbt && uv run dbt run

dbt-run-staging: ## Run staging-layer dbt models only
	cd dbt && uv run dbt run --select staging

dbt-test: ## Run all dbt tests
	cd dbt && uv run dbt test

dbt-full-refresh: ## Recompute all dbt models from scratch
	cd dbt && uv run dbt run --full-refresh

run: ## Run an arbitrary CLI command — usage: make run ARGS="ingest-csv --help"
	@test -n "$(ARGS)" || (echo 'ARGS is required — e.g. make run ARGS="ingest-csv --help"' && exit 1)
	$(CLI) $(ARGS)

clean: ## Remove Python bytecode caches
	find . -type d -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} +
