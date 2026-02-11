#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# K2 Market Data Platform - Run All Iceberg DDL
# Purpose: Execute all Iceberg DDL scripts in order
# Version: Final Working Configuration (ADR-013)
# Catalog: Hadoop (file-based, zero dependencies)
# FileIO: HadoopFileIO (local filesystem)
# Last Updated: 2026-02-11 (after 3+ hours troubleshooting)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -e  # Exit on error

echo "=========================================="
echo "K2 Iceberg DDL Execution"
echo "Spark 3.5.5 + Iceberg 1.x (tabulario)"
echo "Hadoop Catalog (file-based)"
echo "=========================================="

# Spark SQL command (bundled in tabulario image)
SPARK_SQL="spark-sql"

# Working configuration (Hadoop catalog + HadoopFileIO)
CATALOG_CONF="--conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog"
CATALOG_CONF="$CATALOG_CONF --conf spark.sql.catalog.demo.type=hadoop"
CATALOG_CONF="$CATALOG_CONF --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse"
CATALOG_CONF="$CATALOG_CONF --conf spark.sql.catalog.demo.io-impl=org.apache.iceberg.hadoop.HadoopFileIO"
CATALOG_CONF="$CATALOG_CONF --conf spark.sql.defaultCatalog=demo"

echo ""
echo "Step 0: Creating 'cold' database namespace..."
$SPARK_SQL $CATALOG_CONF -e "CREATE DATABASE IF NOT EXISTS demo.cold; SHOW DATABASES IN demo;"
echo "✓ cold database created"

echo ""
echo "Step 1: Creating Bronze Tables (2 tables)..."
$SPARK_SQL $CATALOG_CONF -f /home/iceberg/ddl/02-bronze-tables.sql
echo "✓ Bronze tables created"

echo ""
echo "Step 2: Creating Silver Table (1 table)..."
$SPARK_SQL $CATALOG_CONF -f /home/iceberg/ddl/03-silver-table.sql
echo "✓ Silver table created"

echo ""
echo "Step 3: Creating Gold Tables (6 tables)..."
$SPARK_SQL $CATALOG_CONF -f /home/iceberg/ddl/04-gold-tables.sql
echo "✓ Gold tables created"

echo ""
echo "=========================================="
echo "All Iceberg tables created successfully!"
echo "=========================================="
echo ""
echo "Verifying tables..."
$SPARK_SQL $CATALOG_CONF -e "SHOW TABLES IN demo.cold;"
echo ""
echo "Table details:"
$SPARK_SQL $CATALOG_CONF -e "
  SELECT
    'bronze_trades_binance' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.bronze_trades_binance
  UNION ALL
  SELECT
    'bronze_trades_kraken' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.bronze_trades_kraken
  UNION ALL
  SELECT
    'silver_trades' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.silver_trades
  UNION ALL
  SELECT
    'gold_ohlcv_1m' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.gold_ohlcv_1m
  UNION ALL
  SELECT
    'gold_ohlcv_5m' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.gold_ohlcv_5m
  UNION ALL
  SELECT
    'gold_ohlcv_15m' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.gold_ohlcv_15m
  UNION ALL
  SELECT
    'gold_ohlcv_30m' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.gold_ohlcv_30m
  UNION ALL
  SELECT
    'gold_ohlcv_1h' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.gold_ohlcv_1h
  UNION ALL
  SELECT
    'gold_ohlcv_1d' as table_name,
    COUNT(*) as row_count
  FROM demo.cold.gold_ohlcv_1d;
"
echo ""
echo "Done!"
