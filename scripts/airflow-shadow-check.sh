#!/usr/bin/env bash
# The schedule-shadow gate (plan phase P2, pull request 1; ADR-034). Run it once
# the airflow-* services have been up beside Prefect for at least the window:
#
#   bash scripts/airflow-shadow-check.sh              # the 2 h window
#   bash scripts/airflow-shadow-check.sh --hours 3
#
# The window ends 10 minutes ago, so its newest slot has had time to finish,
# and holds 12 slots an hour of lake_ingest's `1-59/5 * * * *`. It asserts:
#
#   (1) every slot in the window has a scheduled Airflow lake_ingest run;
#   (2) every one of those runs succeeded (ingest.py --probe: it writes nothing);
#   (3) Prefect's lake-ingest runs expected in the same window all COMPLETED,
#       one per slot, so the shadow did not disturb the writer.
#
# Exit 0 only if all three hold. Quote the result with its window: "2 h".
# A gate before the cutover only, which the cutover runbook cites
# (docs/runbooks/airflow-scheduling.md): once Prefect is stopped, check (3)
# fails by design. It goes with Prefect at the archive pull request.
# The verdict is scripts/airflow_shadow_check.py, tested without a stack.
# Needs the stack up and .env loaded the way scripts/health.sh loads it.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

HOURS=2
if [ "${1:-}" = "--hours" ]; then HOURS="${2:?--hours needs a number}"; fi

set -a
# shellcheck disable=SC1091 # .env is generated locally from .env.example
. ./.env
set +a
PREFECT_PORT=${K2_PREFECT_PORT:-4200}

window=$(python3 -c '
import sys
from datetime import datetime, timedelta, timezone
end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=10)
start = end - timedelta(hours=float(sys.argv[1]))
print(start.isoformat(), end.isoformat())' "$HOURS")
read -r start end <<< "$window"
echo "window: $start .. $end (${HOURS} h)"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# The Airflow CLI can print warnings on stdout; the JSON is the last line.
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler \
  airflow dags list-runs lake_ingest -o json --start-date "$start" --end-date "$end" 2>/dev/null \
  | tail -n 1 > "$tmp/airflow.json"

curl -sf --max-time 20 -X POST "localhost:${PREFECT_PORT}/api/flow_runs/filter" \
  -H 'Content-Type: application/json' \
  -d "{\"limit\":200,\"flows\":{\"name\":{\"any_\":[\"lake-ingest\"]}},\"flow_runs\":{\"expected_start_time\":{\"after_\":\"$start\",\"before_\":\"$end\"}}}" \
  > "$tmp/prefect.json"

python3 scripts/airflow_shadow_check.py "$start" "$end" "$tmp/airflow.json" "$tmp/prefect.json"
