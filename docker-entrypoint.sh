#!/usr/bin/env bash
set -euo pipefail

# Build a cron schedule from RUN_TIME (HH:MM) so the schedule stays a single
# source of truth in .env. supercronic — not the Python process — owns timing.
RUN_TIME="${RUN_TIME:-08:00}"
HH="${RUN_TIME%%:*}"
MM="${RUN_TIME##*:}"
# 10# forces base-10 so leading zeros (e.g. "08") aren't read as octal
CRON_EXPR="$((10#$MM)) $((10#$HH)) * * *"

CRONTAB=/tmp/jobbot.cron
echo "${CRON_EXPR} cd /app && python run.py --now" > "${CRONTAB}"

echo "[entrypoint] Timezone : ${TZ:-UTC}"
echo "[entrypoint] Schedule : daily at ${RUN_TIME} (cron: ${CRON_EXPR})"

# Optional immediate run on container start (off by default so a restart/reboot
# does not fire an unexpected digest email). Enable with RUN_ON_START=true.
if [ "${RUN_ON_START:-false}" = "true" ]; then
    echo "[entrypoint] RUN_ON_START=true -> running the pipeline once now"
    python run.py --now || echo "[entrypoint] initial run failed; continuing to schedule"
fi

echo "[entrypoint] starting supercronic scheduler"
exec supercronic "${CRONTAB}"
