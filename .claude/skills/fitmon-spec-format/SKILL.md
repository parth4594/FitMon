---
name: fitmon-spec-format
description: Use whenever drafting a new FitMon implementation spec (Claude chat) or before implementing a spec file pasted into the repo (Claude Code). Enforces the FitMon spec-driven-development template — section order, required subsections, and standing project rules (uv only, no ORM, parameterized SQL, .env-only credentials, idempotent upserts). Trigger on: "draft a spec for...", "write a spec...", a pasted spec_*.md file, or "implement spec_*.md".
---

# FitMon Spec Format

FitMon uses spec-driven development: every feature is specified in a `.md` file
before Claude Code implements it. This skill enforces one consistent structure
across two surfaces:

- **Drafting gate** (Claude chat) — when generating a new spec, follow this
  skeleton so the output is implementation-ready without manual reformatting.
- **Validation gate** (Claude Code) — before implementing a spec file found in
  the repo, check it against this skeleton. If required sections are missing
  or malformed, stop and report what's missing rather than improvising to
  fill the gap.

Reference `spec_template.md` in this skill's directory for the exact skeleton
and section ordering.

## Required sections, in order

1. **Overview** — what this spec covers, and an explicit line on what it does
   NOT cover (prevents scope creep during implementation).
2. **Depends on** — prior specs, tables, or env vars this spec assumes exist.
   Write "Nothing — this is the first step." if none.
3. **Entry point(s)** — CLI commands added/changed, with example invocations.
   Write "No new routes/commands" if none.
4. **Database schema** *(omit heading entirely if no DB changes)* — one
   markdown table per table/column-set touched: Column | Type | Constraints
   (add a Source field column for API/CSV-mapped specs).
5. **Functions / logic to implement** — signatures and field-mapping tables
   or pseudocode blocks. This is the core "what to build."
6. **Feature-specific implementation details** — anything that doesn't fit
   the generic sections above: client-layer constants, retry/backoff rules,
   sync-mode branching, specific SQL patterns, one-time migration/backfill
   procedures, cron/scheduling setup. Use sub-headings (###) freely inside
   this section — it's the flexible slot so the top-level skeleton stays
   identical across every spec regardless of feature complexity.
7. **Dependencies** — new packages via `uv add <pkg>` only. Write "No new
   packages required" if none. Never mention pip/poetry.
8. **Files to create or change** — table: File | Action.
9. **New environment variables** *(omit if none)* — dotenv block plus a note
   that `.env` is gitignored and credentials are never hardcoded.
10. **Rules for implementation** — pull applicable standing rules from
    CLAUDE.md (see below) plus anything spec-specific.
11. **Unit tests** *(omit if not applicable)* — table: Test | What it covers.
12. **Definition of done** — checkbox list. Always end with these three
    housekeeping items unless the spec explicitly says otherwise:
    - [ ] README.md updated with new entrypoints
    - [ ] Makefile updated with new entrypoints
    - [ ] state.md updated with 1–2 lines on the new pipeline state
13. **Misc / Manual additions** — always present, always last, left EMPTY by
    the drafting gate. This is reserved for the user to fill in by hand after
    reviewing the generated draft. Never populate this section on generation.

## Standing rules to check for in section 10 (from CLAUDE.md)

Pull only the ones relevant to what the spec touches:

- No ORM — psycopg2 / raw SQL only
- All SQL parameterized — never f-strings
- No FK constraints in `raw` schema — dbt tests enforce integrity
- Credentials from `.env` only, via `pydantic-settings` — never hardcoded
- Ingestion is idempotent — `ON CONFLICT DO UPDATE`, never blind insert
- DB connection/upsert logic lives only in `src/db/postgres.py`
- `uv` exclusively — never pip or poetry
- Cleaning/renaming/aggregation belongs in dbt, never in ingestion scripts

## Validation gate behavior (Claude Code)

When asked to implement a spec file:

1. Open the file and check sections 1, 2, 3, 5, 7, 8, 10, 12 are present
   (these are mandatory; 4, 6, 9, 11 are conditional on content).
2. Check the Definition of Done list ends with the three housekeeping items.
3. If anything mandatory is missing or a table is malformed (e.g. schema
   table missing a Constraints column), stop and report exactly what's
   missing — do not guess or fill gaps by inference.
4. If structurally valid, proceed to implementation as normal.

This is a structural check only — not a re-evaluation of the engineering
decisions inside the spec. Content correctness remains the user's judgment
call at drafting time.