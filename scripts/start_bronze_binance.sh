#!/bin/bash
# Start Bronze Ingestion Job - Binance
# Streams raw Binance data from Kafka → Bronze Iceberg

set -e

echo "Starting Bronze Ingestion Job - Binance..."
echo "Source: Kafka topic market.crypto.trades.binance.raw"
echo "Target: bronze_binance_trades (Iceberg)"
echo ""

docker exec k2-spark-master bash -c "cd /opt/k2 && /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 2 \
  --packages org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.4.0,org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,org.apache.spark:spark-avro_2.12:3.5.0 \
  --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1' \
  --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1' \
  src/k2/spark/jobs/streaming/bronze_ingestion_binance.py"
