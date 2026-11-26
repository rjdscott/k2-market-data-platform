#!/usr/bin/env bash
# N0-4 (b): Spark expire_snapshots by snapshot id on a Trino-written table, with scoped keys.
# Throwaway Trino 484 and Spark (k2-spark-iceberg:3.5.5_1.8.1, 512m driver) on k2-net through
# the live Lakekeeper; scratch schema spike_n04plat_b in warehouse fx_reference; five scratch
# MinIO users and policies spike-n04plat-b-p0..p4. Removes all of it afterwards.
set -euo pipefail
exec python3 -B "$(dirname "$0")/spike.py" "$@"  # -B: no __pycache__ in the checkout
