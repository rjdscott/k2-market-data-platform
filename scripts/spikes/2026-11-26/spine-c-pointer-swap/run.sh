#!/usr/bin/env bash
# N0-4 (c): the release pointer swap through a read-only directory mount on this host.
# Starts a throwaway Trino 484 and Airflow 3.3.2 dag-processor (no live service touched), swaps
# the pointers SWAPS times (default 5) in each of three ways (on the host, on the host then a
# listing, inside the VM), measures, cleans up, prints the finding. About 25 minutes: a host-side
# swap is never seen, so each of those waits HOST_TIMEOUT (default 120) seconds.
# AIRFLOW_REFRESH_INTERVAL (default 30; the live stack runs Airflow's 300) sets
# [dag_processor] refresh_interval for the throwaway dag-processor.
set -euo pipefail
exec python3 -B "$(dirname "$0")/spike.py" "$@"  # -B: no __pycache__ in the checkout
