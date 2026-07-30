#!/bin/bash
# Invoked hourly by launchd (launchd/com.fitmon.pipeline-health-check.plist)
# to warn by email if sync-hevy hasn't run in 25+ hours. Best-effort: while
# the machine is asleep this simply doesn't run, same as any launchd job —
# it fires as soon as possible after wake instead.
set -euo pipefail
cd "$(dirname "$0")/.."
exec /Users/parth/.local/bin/uv run python -m src.cli check-pipeline-health
