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
├── .claude/
│   ├── agents/
│   ├── skills/
│   └── specs/
│
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
├── notebooks/                    # Jupyter EDA (exploratory only, never imported)
├── scripts/                      # One-off utility scripts
│
├── src/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── cli.py                # CLI entrypoints (ingest, sync, run pipeline)
│   │   ├── helpers.py            # Shared app-level utilities
│   │   └── pipeline_runner.py    # Orchestrates ingestion → dbt
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── dbt_config.py
│   │   ├── hevy_ingestion_config.py
│   │   ├── logging_config.py
│   │   └── postgres_config.py
│   │
│   ├── domain/
│   │   ├── models/               # Entities + value objects (Workout, Set, HealthMetric)
│   │   ├── ports/                # Abstract repository interfaces
│   │   ├── services/             # Pure business logic (e1RM, volume, PR detection)
│   │   └── usecases/             # Orchestration (ingest CSV, sync API, parse health)
│   │
│   └── infrastructure/
│       ├── adapters/             # Hevy API client, Apple Health XML parser
│       ├── ingestion/            # CSV + XML → domain entities
│       ├── schema/               # Raw table SQL definitions (migrations)
│       └── utils/
│           └── postgres_helpers.py  # Connection pool, upsert helpers
│
├── tests/
│   ├── unit/                     # Domain logic only — no DB, no network
│   ├── integration/              # Repository + Supabase (test schema)
│   └── e2e/                      # Full pipeline runs
│
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml
├── .python-version
└── pyproject.toml                # uv-managed dependencies
```

**Where things belong:**
- DB connection + upsert helpers → `infrastructure/utils/postgres_helpers.py` only
- External API/file parsing → `infrastructure/adapters/` and `infrastructure/ingestion/`
- Pure business logic → `domain/services/` — no I/O, no DB imports
- Orchestration → `domain/usecases/` — calls services + repositories
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
- dbt model naming: prefix enforced — `stg_`, `fct_`, `dim_`
- All Postgres queries: parameterized only — never f-strings in SQL
- Ingestion: idempotent by default — always upsert, never blind insert
- Secrets: loaded from `.env` via `python-dotenv` — never hardcoded
- `domain/` must never import from `infrastructure/` — ever

---
## Tech constraints

- **Supabase (Postgres) only** — no SQLite, no other databases
- **dbt Core only** — no dbt Cloud, no paid tier
- **Python 3.11+** — f-strings and `match` statements are fine
- **No ORM** — raw SQL via `psycopg2` only
- **uv only** — no pip, no poetry; all dependency changes go through `uv`
- **No new packages** without updating `pyproject.toml` and flagging it

---
## Subagent policy

- Always use an explore subagent to read the relevant model or script
  before modifying any existing logic
- Always use a subagent to run `dbt test` and verify no broken refs
  after any model change
- When asked to plan a new pipeline or model, delegate codebase research
  to a subagent before presenting the plan
- Always use a plan subagent in plan mode before touching `marts/`

---
## Commands

```bash
# Setup
uv sync                                      # Install all dependencies from pyproject.toml
uv add <package>                             # Add a new dependency
uv run <command>                             # Run any command in the project venv

# Ingestion
uv run python -m app.cli ingest-csv --file path/to/hevy_export.csv
uv run python -m app.cli sync-hevy
uv run python -m app.cli parse-health --file path/to/export.xml

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
uv run pytest tests/e2e
```

---
## Implementation status

| Component | Status |
|---|---|
| `infrastructure/schema/` raw table SQL | Stub |
| `infrastructure/ingestion/` CSV loader | Stub |
| `infrastructure/adapters/` Hevy API client | Stub |
| `infrastructure/ingestion/` Apple Health parser | Stub |
| `infrastructure/utils/postgres_helpers.py` | Stub |
| `dbt/seeds/dim_exercises.csv` | Stub |
| `dbt/models/staging/stg_workouts.sql` | Stub |
| `dbt/models/staging/stg_sets.sql` | Stub |
| `dbt/models/marts/fct_strength_prs.sql` | Stub |
| `dbt/models/marts/fct_workout_volume.sql` | Stub |
| `dbt/models/marts/fct_workout_frequency.sql` | Stub |

**Do not implement a stub unless the active task explicitly targets it.**

---
## Warnings and things to avoid

- **Never put DB logic in `app/`** — it belongs in `infrastructure/utils/postgres_helpers.py`
- **Never clean data in ingestion scripts** — raw tables must be exact copies of source data
- **Never hardcode Supabase credentials** — always read from `.env`
- **Never use `INSERT` without conflict handling** — all ingestion must use `ON CONFLICT DO UPDATE`
- **Never put aggregation logic in staging models** — staging cleans, marts aggregate
- **Never import `infrastructure` from `domain/`** — dependency direction is domain ← usecase ← infra
- **Never rename `dim_exercises.csv` columns** — downstream models depend on:
  `exercise_name`, `canonical_name`, `muscle_group`, `movement_pattern`, `equipment`
- **`raw` schema has no foreign keys** — referential integrity is enforced in dbt tests only
- **Apple Health XML is large** — always stream-parse with `iterparse`, never load into memory
- **Hevy API rate limit** — add a 1-second delay between paginated requests
- **Never use `pip` or `poetry`** — this project uses `uv` exclusively