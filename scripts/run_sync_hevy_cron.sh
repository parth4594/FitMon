#!/bin/bash
# Invoked by launchd (launchd/com.fitmon.sync-hevy.plist) daily at 14:00
# Europe/Berlin. launchd does not source shell profiles, so PATH is not
# guaranteed to include uv — call it by absolute path. If the machine was
# asleep at 14:00, launchd runs this the moment it wakes instead of
# skipping it; src/pipeline.py detects and flags that lateness.
set -euo pipefail

for i in $(seq 1 30); do
    ping -c 1 -W 1 8.8.8.8 > /dev/null 2>&1 && break
    sleep 1
done

cd "$(dirname "$0")/.."
exec /Users/parth/.local/bin/uv run python -m src.cli sync-hevy --mode auto --trigger-source cron
