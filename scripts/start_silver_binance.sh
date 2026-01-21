#!/bin/bash
# Start Silver Binance Transformation Job v2 (Bronze → Silver with Schema Registry + Professional Partitioning)
#
# Features:
# - Fetches V2 schema from Schema Registry (exact match with producers)
# - Professional partitioning: (exchange_date, symbol) for optimal queries
# - PERMISSIVE mode: Handles malformed Avro gracefully
# - Validation: price > 0, quantity > 0, timestamp not null, message_id present

docker exec k2-spark-master bash -c "cd /opt/k2 && AWS_REGION=us-east-1 /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --total-executor-cores 2 \
  --executor-cores 2 \
  --conf spark.driver.extraJavaOptions='-Daws.region=us-east-1' \
  --conf spark.executor.extraJavaOptions='-Daws.region=us-east-1' \
  --jars /opt/spark/jars-extra/iceberg-spark-runtime-3.5_2.12-1.4.0.jar,/opt/spark/jars-extra/iceberg-aws-1.4.0.jar,/opt/spark/jars-extra/bundle-2.20.18.jar,/opt/spark/jars-extra/url-connection-client-2.20.18.jar,/opt/spark/jars-extra/hadoop-aws-3.3.4.jar,/opt/spark/jars-extra/spark-avro_2.12-3.5.3.jar \
  src/k2/spark/jobs/streaming/silver_transformation_v2.py binance"
