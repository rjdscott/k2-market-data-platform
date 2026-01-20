#!/bin/bash
set -e

echo "======================================================================"
echo "Submitting Flink Minimal DataStream Test Job"
echo "======================================================================"

# Wait for JobManager
echo "Waiting for Flink JobManager at flink-jobmanager:8081..."
until curl -sf http://flink-jobmanager:8081 > /dev/null; do
  sleep 2
done
echo "✓ JobManager ready"

# Submit the test job
echo "Submitting minimal DataStream test job..."
/opt/flink/bin/flink run \
  --class com.k2.flink.test.KafkaReadTestMinimal \
  --detached \
  /opt/flink/usrlib/flink-bronze-jobs-1.0.0.jar

echo "======================================================================"
echo "✓ Test job submitted successfully"
echo "======================================================================"
echo "Watch output in TaskManager logs:"
echo "  docker logs k2-flink-taskmanager-1 -f | grep -A 2 -B 2 '@'"
echo "======================================================================"
