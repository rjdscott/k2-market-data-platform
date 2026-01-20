#!/bin/bash
set -euo pipefail

JARS_DIR="flink-jars"
mkdir -p "$JARS_DIR"
cd "$JARS_DIR"

echo "========================================="
echo "Flink Connector JARs Download Script"
echo "========================================="
echo ""
echo "Target directory: $(pwd)"
echo "Flink version: 1.19.1"
echo "Kafka connector version: 3.3.0-1.19"
echo "Iceberg version: 1.7.1"
echo ""

# Kafka SQL Connector
echo "[1/4] Downloading Kafka SQL Connector..."
curl -# -L -O https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.3.0-1.19/flink-sql-connector-kafka-3.3.0-1.19.jar

# Iceberg Flink Runtime (large file, ~200MB)
echo "[2/4] Downloading Iceberg Flink Runtime (this may take a while)..."
curl -# -L -O https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-flink-runtime-1.19/1.7.1/iceberg-flink-runtime-1.19-1.7.1.jar

# S3 Filesystem Hadoop
echo "[3/4] Downloading S3 Filesystem Hadoop..."
curl -# -O https://repo1.maven.org/maven2/org/apache/flink/flink-s3-fs-hadoop/1.19.1/flink-s3-fs-hadoop-1.19.1.jar

# Prometheus Metrics Reporter
echo "[4/4] Downloading Prometheus Metrics Reporter..."
curl -# -O https://repo1.maven.org/maven2/org/apache/flink/flink-metrics-prometheus/1.19.1/flink-metrics-prometheus-1.19.1.jar

echo ""
echo "========================================="
echo "Download complete! Verifying files..."
echo "========================================="
ls -lh

echo ""
echo "Total size:"
du -sh .

echo ""
echo "✅ JAR files ready. Add to Flink cluster via docker-compose volume mounts."
