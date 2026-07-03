# FitMon 🏋️

**Fitness Monitor** — a personal, self-hosted workout tracking and real-time optimization system backed by science.

---

## Overview

FitMon is a project designed to replace commercial fitness apps with a fully owned, data-driven training platform. It tracks workouts, analyzes performance in real time, and delivers evidence-based suggestions to optimize training — all without vendor lock-in.

---

## Goals

- **Track workouts** — log exercises, sets, reps, and weight with a simple interface
- **Real-time analysis** — surface insights during and after each session
- **Science-backed optimization** — suggest progressive overload, deload weeks, and exercise swaps grounded in sports science research

---

## Tech Stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL (via Supabase) |
| Transformations | dbt Core |
| Data ingestion | Strong CSV export / Hevy API (planned) |
| Health metrics | Apple Health XML export |
| Bot interface | Telegram bot + Claude API |
| Dashboard | Grafana |
| Hosting | Railway / Render (free tier) |

---

## Data Sources

- **Strong** — historical workout data via CSV export
- **Hevy** — planned migration (official public API)
- **Apple Health / Apple Watch** — steps, heart rate, sleep, active calories


---

## Getting Started

> Prerequisites: Docker, Python 3.11+, Node.js 18+, [uv](https://docs.astral.sh/uv/) (dependency manager — no pip, no poetry)

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/FitMon.git
cd FitMon

# Start local Supabase (Postgres)
supabase start

# Install Python dependencies from pyproject.toml
uv sync

# Configure Supabase credentials (never hardcoded — read from .env)
cp .env.example .env   # then fill in SUPABASE_DB_HOST / PORT / NAME / USER / PASSWORD
```

---

## CLI Usage

### Quickest path: `make`

A `Makefile` wraps the most common commands so you don't have to type
`uv run python -m src.cli ...` every time. Run `make` or `make help` to list
everything available:

```bash
make help                                                          # List all targets
make install                                                       # uv sync
make setup-db                                                      # Create all schemas and tables in Supabase
make ingest FILE=path/to/export.csv SOURCE=strong                  # Ingest a CSV (language auto-detected)
make ingest FILE=path/to/export.csv SOURCE=strong LANG_CODE=de     # ...with an explicit language
make ingest FILE=path/to/export.csv SOURCE=strong DEBUG=1          # ...with verbose console logging
make test                                                          # uv run pytest tests/unit
make test-integration                                              # uv run pytest tests/integration
make dbt-seed                                                      # cd dbt && uv run dbt seed
make dbt-run                                                       # cd dbt && uv run dbt run
make dbt-test                                                      # cd dbt && uv run dbt test
```

For anything not covered by a fixed target (extra flags, future subcommands,
`--help` itself), pass the raw arguments through `make run`:

```bash
make run ARGS="--help"
make run ARGS="ingest-csv --help"
make run ARGS="ingest-csv --file path/to/export.csv --source strong --lang de"
```

> `LANG_CODE` (not `LANG`) is used for the `--lang` value because `LANG` is
> your shell's locale environment variable — `make` auto-imports environment
> variables, so reusing `LANG` would silently leak your locale (e.g.
> `C.UTF-8`) in as the language code.

### Full command reference (no `make` required)

Every pipeline command runs through `src/cli.py`. Full flag reference is
always available via `--help` (or the short alias `-h`) on the CLI itself or
on any subcommand:

```bash
uv run python -m src.cli --help
uv run python -m src.cli ingest-csv --help
uv run python -m src.cli setup-db --help
```

**Setup**

```bash
uv sync                                      # Install all dependencies from pyproject.toml
uv add <package>                             # Add a new runtime dependency
uv add --dev <package>                       # Add a new dev-only dependency (e.g. pytest, ruff)
uv run python -m src.cli setup-db            # Create all schemas and tables in Supabase
```

**Ingestion**

```bash
# Explicit source and language (recommended)
uv run python -m src.cli ingest-csv --file path/to/export.csv --source strong --lang de

# Auto-detect language from the CSV header row (--source is always required)
uv run python -m src.cli ingest-csv --file path/to/export.csv --source strong

# Future sources (e.g. Hevy API, Apple Health) once implemented:
uv run python -m src.cli sync-hevy
uv run python -m src.cli parse-health --file path/to/export.xml
```

**Logging.** By default the console shows `INFO` and above; every run also
appends full `DEBUG`-level detail to `logs/pipeline_<YYYY-MM-DD>.log`
(one file per day, gitignored). Pass `--debug-mode` on the top-level CLI
(before the subcommand) for verbose console output too — useful when
debugging a failed run:

```bash
uv run python -m src.cli --debug-mode ingest-csv --file path/to/export.csv --source strong
uv run python -m src.cli --info-mode  ingest-csv --file path/to/export.csv --source strong   # default
```

**dbt**

```bash
cd dbt
uv run dbt seed                              # Load dim_exercises.csv
uv run dbt run                               # Run all models
uv run dbt run --select staging              # Staging layer only
uv run dbt run --select fct_strength_prs     # Single model
uv run dbt test                              # Run all tests
uv run dbt run --full-refresh                # Recompute from scratch
```

**Tests**

```bash
uv run pytest tests/unit
uv run pytest tests/integration
```

---

## Roadmap

- [x] Supabase schema setup
- [x] Strong CSV import script
- [ ] Apple Health XML parser
- [ ] Telegram bot for ongoing workout logging
- [ ] dbt transformation models
- [ ] Grafana dashboard (PRs, volume, frequency heatmap)
- [ ] Real-time workout analysis via Claude API
- [ ] Migrate to Hevy API

---

## License

MIT — personal use project. Use freely, adapt as needed.