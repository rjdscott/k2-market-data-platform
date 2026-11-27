#!/usr/bin/env bash
# spine-h-tested-data-view: the gate of phase 001 step S01 (decision O8). h1 the fence matrix, h2
# the polled cycle, h3 the catalog fence; one result table; exit non-zero on any leak or failed
# pass criterion (docs/research/2026-11-26-tested-data-view-gate.md).
#
# Needs: the running stack's Lakekeeper (host port 18181) and MinIO on k2-net; MINIO_ROOT_USER and
# MINIO_ROOT_PASSWORD in the main checkout's .env (or K2_ENV_FILE); images trinodb/trino:484,
# quay.io/lakekeeper/catalog:v0.13.9, the compose file's Postgres digest and MinIO tag, and the
# Spark base image of docker/spark/Dockerfile (for python3); 6 GiB available; about 10 minutes.
# Writes only scratch warehouse spike_s01, bucket spike-s01, network spike-s01-catalog and
# containers spike-s01-*, all removed on exit.
set -euo pipefail
exec python3 -B "$(dirname "$0")/spike.py" "$@"  # -B: no __pycache__ in the checkout
