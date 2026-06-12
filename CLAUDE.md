# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FitMon is an early-stage fitness/nutrition tracker. Python 3.14, managed with `uv`, backed by DuckDB. As of now, most modules are scaffolding — `main.py`, `src/domain/services.py`, and `src/infrastructure/db.py` are empty or near-empty stubs. The schema and domain models are the only substantive code.

## Commands

Dependencies are managed with `uv` (see `uv.lock`, `pyproject.toml`):

- Install / sync deps: `uv sync`
- Run the entrypoint: `uv run python main.py`
- Add a dependency: `uv add <pkg>`
- Open a REPL with deps available: `uv run python`

There is no test, lint, or build configuration yet — do not assume `pytest`, `ruff`, etc. are wired up unless you add them.

## Architecture

The `src/` tree is laid out in DDD-style layers. Keep new code in the layer that matches its role rather than collapsing them.

- `src/domain/` — pure dataclass models and (eventually) business logic. `models.py` defines `Meal`, `Workout`, `WorkoutExercise`, `WorkoutSet`. Each model mirrors a table in `infrastructure/schema.py` and exposes `from_record(dict)` / `to_dict()` for DB ↔ plain-data conversion. Parsing helpers (`_parse_uuid`, `_parse_datetime`, `_parse_date`) are tolerant: they accept native types or ISO-8601 strings and return `None` on failure rather than raising. `services.py` is currently empty and is the intended home for domain logic that operates on these models.
- `src/infrastructure/` — DuckDB persistence. `schema.py` holds the canonical `CREATE TABLE IF NOT EXISTS` SQL and an `init_db()` that runs it via a `connect()` context manager imported from `db.py`. `db.py` is currently empty — `connect()` needs to be implemented before `init_db()` or any persistence will run.
- `src/application/` — placeholder for use-case / orchestration code wiring domain ↔ infrastructure. Empty today.

### Schema invariants worth knowing

- `meals` uses an `INTEGER` PK; everything workout-related uses `UUID` PKs.
- `workout_exercises.workout_id` FKs `workouts`; `workout_sets.workout_exercise_id` FKs `workout_exercises`. When adding queries or mutations, respect this ordering on inserts/deletes.
- The domain models' field names are kept 1:1 with column names. If you change a column, update the matching dataclass + its `from_record`/`to_dict` together.
