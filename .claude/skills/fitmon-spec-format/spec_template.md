# Spec: <Feature Name>

## 1. Overview

[What this spec covers, in a short paragraph or numbered list.]

This spec does not cover [explicitly out-of-scope items].

---

## 2. Depends on

- <prior spec files, tables, or env vars required>
- Write "Nothing — this is the first step." if none.

---

## 3. Entry point(s)

```bash
uv run python -m src.cli <command> --<flag>
```

Write "No new routes/commands" if none.

---

## 4. Database schema

<!-- Omit this entire heading if no DB changes -->

### A. `<table_or_amendment_name>`

| Column | Type | Constraints | Source field |
|---|---|---|---|
| | | | |

---

## 5. Functions / logic to implement

### `<module_path>` — `<function_name>()`

- <behavior bullet>
- <behavior bullet>

```python
def <function_name>(...) -> ...:
    ...
```

<Field-mapping table or pseudocode block if applicable.>

---

## 6. Feature-specific implementation details

<!-- Flexible slot. Use ### sub-headings freely for anything that doesn't
     fit sections 1-5: client constants, retry/backoff rules, sync-mode
     branching, SQL upsert patterns, migration/backfill procedures,
     cron/scheduling. Keep the top-level skeleton identical across specs
     by putting spec-unique mechanics here instead of new top-level headings. -->

---

## 7. Dependencies

```bash
uv add <package>
```

Write "No new packages required" if none. Never pip/poetry.

---

## 8. Files to create or change

| File | Action |
|---|---|
| | |

---

## 9. New environment variables

<!-- Omit if none -->

```dotenv
NEW_VAR=placeholder
```

`.env` is gitignored. Never commit credentials.

---

## 10. Rules for implementation

- <pull relevant standing rules from CLAUDE.md>
- <spec-specific rules>

---

## 11. Unit tests

<!-- Omit if not applicable -->

| Test | What it covers |
|---|---|
| | |

---

## 12. Definition of done

- [ ] <feature-specific criteria>
- [ ] All unit tests pass
- [ ] No credentials in committed files
- [ ] No SQL f-strings anywhere in the codebase
- [ ] README.md updated with new entrypoints
- [ ] Makefile updated with new entrypoints
- [ ] state.md updated with 1–2 lines on the new pipeline state

---

## 13. Misc / Manual additions

<!-- Leave empty on generation. Reserved for manual notes after review. -->