#!/bin/bash
# Submit Flink job to cluster via REST API

set -e

JOB_CLASS="$1"
JOB_NAME="$2"

if [ -z "$JOB_CLASS" ] || [ -z "$JOB_NAME" ]; then
    echo "Usage: $0 <job-class> <job-name>"
    exit 1
fi

JOBMANAGER="${JOB_MANAGER_RPC_ADDRESS:-flink-jobmanager}"
JAR_PATH="/opt/flink/usrlib/flink-bronze-jobs-1.0.0.jar"

echo "======================================================================"
echo "Submitting Flink Bronze Job: $JOB_NAME"
echo "======================================================================"

# Wait for JobManager to be ready
echo "Waiting for Flink JobManager at $JOBMANAGER:8081..."
for i in {1..60}; do
    if curl -sf "http://$JOBMANAGER:8081/config" > /dev/null 2>&1; then
        echo "✓ JobManager ready"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "✗ JobManager not available after 60 attempts"
        exit 1
    fi
    [ $((i % 10)) -eq 0 ] && echo "  Attempt $i/60..."
    sleep 2
done

# Submit job using flink run
echo "Submitting job..."
/opt/flink/bin/flink run \
    -d \
    -m "$JOBMANAGER:8081" \
    -c "$JOB_CLASS" \
    "$JAR_PATH"

echo ""
echo "======================================================================"
echo "✓ Job Submitted Successfully"
echo "======================================================================"
echo "Monitor at: http://localhost:8082"
echo "======================================================================"

# Keep container alive
echo "Job is running... (container will stay alive)"
tail -f /dev/null
