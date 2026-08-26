#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# K2 Market Data Platform - Bootstrap Iceberg cold-tier tables
#
# Run by the one-shot `iceberg-init` compose service before prefect-worker
# starts, so the first offload never hits TABLE_OR_VIEW_NOT_FOUND.
#
# Idempotent: every statement is CREATE ... IF NOT EXISTS, so re-running on an
# existing warehouse is a no-op. Exits non-zero if any statement fails.
#
# Catalog config must stay in sync with docker/offload/offload_generic.py.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -euo pipefail

DDL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WAREHOUSE="${ICEBERG_WAREHOUSE:-/home/iceberg/warehouse}"

# ponytail: one spark-sql JVM for all files instead of one per file — startup
# dominates the runtime (~20s vs ~2s of actual DDL).
BUNDLE="$(mktemp /tmp/k2-iceberg-ddl.XXXXXX.sql)"
trap 'rm -f "$BUNDLE"' EXIT

cat "$DDL_DIR"/01-catalog-schema.sql \
    "$DDL_DIR"/02-bronze-tables.sql \
    "$DDL_DIR"/03-silver-table.sql \
    "$DDL_DIR"/04-gold-tables.sql > "$BUNDLE"
echo "SHOW TABLES IN cold;" >> "$BUNDLE"

echo "=========================================="
echo "K2 Iceberg DDL bootstrap"
echo "  catalog:   k2 (hadoop)"
echo "  warehouse: $WAREHOUSE"
echo "=========================================="

spark-sql \
  --driver-memory 512m \
  --conf spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions \
  --conf spark.sql.catalog.k2=org.apache.iceberg.spark.SparkCatalog \
  --conf spark.sql.catalog.k2.type=hadoop \
  --conf "spark.sql.catalog.k2.warehouse=$WAREHOUSE" \
  --conf spark.sql.catalog.k2.io-impl=org.apache.iceberg.hadoop.HadoopFileIO \
  --conf spark.sql.defaultCatalog=k2 \
  --conf spark.ui.enabled=false \
  -f "$BUNDLE"

echo "✓ Iceberg cold-tier tables ready"
