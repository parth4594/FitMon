# CLAUDE.md

## Project overview
FitMon (Fitness Monitor) is a self-hosted personal fitness analytics platform.
It ingests workout data from Hevy (CSV + API) and biometric data from Apple Health,
stores everything in Supabase (Postgres), transforms it with dbt Core, and serves
dashboards via Grafana.

---
## Architecture

```
FitMon/
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml              # Supabase connection (never commit secrets)
│   ├── seeds/
│   │   └── dim_exercises.csv     # Canonical exercise names + muscle group mapping
│   ├── models/
│   │   ├── staging/              # stg_workouts.sql, stg_sets.sql, stg_health.sql
│   │   ├── marts/                # fct_workout_volume, fct_strength_prs, fct_frequency
│   │   └── dimensions/           # dim_exercises.sql, dim_muscles.sql
│   └── tests/
│
├── docs/                         # Architecture notes, data dictionaries
├── logs/                         # Runtime logs (gitignored)
├── notebooks/                    # Jupyter EDA (exploratory only, never imported by src)
├── utils/                        # One-off utility scripts
│
├── src/
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py           # All config via pydantic-settings + .env
│   │   └── logging_config.py
│   │
│   ├── models.py                 # Plain dataclasses: Workout, Set, HealthMetric
│   │
│   ├── services/                 # Pure functions — no I/O, no DB
│   │   ├── __init__.py
│   │   ├── metrics.py            # e1RM, volume, tonnage calculations
│   │   └── pr_detection.py       # PR detection logic
│   │
│   ├── ingestion/                # Reads raw sources → writes to raw schema
│   │   ├── __init__.py
│   │   ├── hevy_csv.py           # CSV → raw.workouts / raw.sets
│   │   ├── hevy_api.py           # Hevy API client → raw.workouts / raw.sets
│   │   └── apple_health.py       # XML → raw.health_metrics
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   └── postgres.py           # Connection pool, upsert helpers
│   │
│   ├── pipeline.py               # Orchestrates ingestion → dbt run
│   └── cli.py                    # CLI entrypoints (ingest, sync, run)
│
├── tests/
│   ├── unit/                     # Pure logic only — no DB, no network
│   └── integration/              # Ingestion + Supabase (test schema)
│
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── .python-version
└── pyproject.toml                # uv-managed dependencies
```

**Where things belong:**
- DB connection + upsert helpers → `src/db/postgres.py` only
- Raw source parsing → `src/ingestion/` — writes untouched data to `raw` schema
- Pure business logic → `src/services/` — no I/O, no DB imports
- Orchestration → `src/pipeline.py`
- Cleaning, renaming, type casting → `dbt/models/staging/` only
- Aggregations → `dbt/models/marts/` only
- Exercise name canonicalization → `dbt/seeds/dim_exercises.csv`
- EDA → `notebooks/` only, never imported by src

---
## Data layer

**Schemas in Supabase:**
- `raw` — untouched source data, written by ingestion scripts
- `staging` — cleaned and renamed by dbt staging models
- `marts` — analytics-ready facts and dimensions, built by dbt

**Raw tables:**
| Table | Source | Key column |
|---|---|---|
| `raw.workouts` | Hevy CSV / API | `hevy_workout_id` |
| `raw.sets` | Hevy CSV / API | `hevy_set_id` |
| `raw.health_metrics` | Apple Health XML | `source_name + start_date` |

**Key dbt models:**
| Model | Layer | Purpose |
|---|---|---|
| `stg_workouts` | staging | Canonical names, parsed timestamps |
| `stg_sets` | staging | Typed weight/reps, unit normalisation |
| `fct_strength_prs` | marts | Per-exercise PR over time |
| `fct_workout_volume` | marts | Weekly volume per muscle group |
| `fct_workout_frequency` | marts | Calendar heatmap data |
| `dim_exercises` | dimensions | Exercise → muscle group → movement pattern |

---
## Code style

- Python: PEP 8, snake_case everywhere
- SQL (dbt): lowercase keywords, CTEs over subqueries, one model per file
- Linting and formatting: ruff — enforced via pre-commit
- dbt model naming: prefix enforced — `stg_`, `fct_`, `dim_`
- All Postgres queries: parameterized only — never f-strings in SQL
- Ingestion: idempotent by default — always upsert, never blind insert
- Secrets: loaded from `.env` via `python-dotenv` — never hardcoded

---
## Tech constraints

- **Supabase (Postgres) only** — no SQLite, no other databases
- **dbt Core only** — no dbt Cloud, no paid tier
- **Python 3.11+** — f-strings and `match` statements are fine
- **No ORM** — raw SQL via `psycopg2` only
- **uv only** — no pip, no poetry; all dependency changes go through `uv`
- **No new packages** without updating `pyproject.toml` and flagging it

---
## Commands

```bash
# Setup
uv sync                                      # Install all dependencies from pyproject.toml
uv add <package>                             # Add a new dependency
uv run <command>                             # Run any command in the project venv
uv add --dev ruff                            # Adding ruff to dependencies

# Ingestion
uv run python -m src.cli ingest-csv --file path/to/hevy_export.csv
uv run python -m src.cli sync-hevy
uv run python -m src.cli parse-health --file path/to/export.xml

# dbt
cd dbt
uv run dbt seed                              # Load dim_exercises.csv
uv run dbt run                               # Run all models
uv run dbt run --select staging              # Staging layer only
uv run dbt run --select fct_strength_prs     # Single model
uv run dbt test                              # Run all tests
uv run dbt run --full-refresh                # Recompute from scratch

# Tests
uv run pytest tests/unit
uv run pytest tests/integration
```

---
## Implementation status

| Component | Status |
|---|---|
| `src/db/postgres.py` | Stub |
| `src/ingestion/hevy_csv.py` | Stub |
| `src/ingestion/hevy_api.py` | Stub |
| `src/ingestion/apple_health.py` | Stub |
| `dbt/seeds/dim_exercises.csv` | Stub |
| `dbt/models/staging/stg_workouts.sql` | Stub |
| `dbt/models/staging/stg_sets.sql` | Stub |
| `dbt/models/marts/fct_strength_prs.sql` | Stub |
| `dbt/models/marts/fct_workout_volume.sql` | Stub |
| `dbt/models/marts/fct_workout_frequency.sql` | Stub |

**Do not implement a stub unless the active task explicitly targets it.**

---
## Warnings and things to avoid

- **Never put DB logic outside `src/db/postgres.py`**
- **Never clean data in ingestion scripts** — raw tables must be exact copies of source data
- **Never hardcode Supabase credentials** — always read from `.env`
- **Never use `INSERT` without conflict handling** — all ingestion must use `ON CONFLICT DO UPDATE`
- **Never put aggregation logic in staging models** — staging cleans, marts aggregate
- **Never rename `dim_exercises.csv` columns** — downstream models depend on:
  `exercise_name`, `canonical_name`, `muscle_group`, `movement_pattern`, `equipment`
- **`raw` schema has no foreign keys** — referential integrity is enforced in dbt tests only
- **Apple Health XML is large** — always stream-parse with `iterparse`, never load into memory
- **Hevy API rate limit** — add a 1-second delay between paginated requests
- **Never use `pip` or `poetry`** — this project uses `uv` exclusively