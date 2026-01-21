#!/bin/bash
# ============================================================================
# Flink Bronze Job Submission via SQL Gateway REST API
# Best Practice: Production-grade approach using curl and jq
# ============================================================================

set -e

GATEWAY_URL="${SQL_GATEWAY_URL:-http://flink-sql-gateway:8083}"
EXCHANGE="$1"
RETRY_COUNT=60
RETRY_DELAY=2

if [ -z "$EXCHANGE" ]; then
    echo "Error: Exchange not specified"
    echo "Usage: $0 <binance|kraken>"
    exit 1
fi

echo "======================================================================"
echo "Flink Bronze Job Submission: ${EXCHANGE^^}"
echo "======================================================================"

# Wait for SQL Gateway
echo "Waiting for SQL Gateway at $GATEWAY_URL..."
for i in $(seq 1 $RETRY_COUNT); do
    if curl -sf "$GATEWAY_URL/v1/info" > /dev/null 2>&1; then
        echo "✓ SQL Gateway ready"
        break
    fi
    if [ $i -eq $RETRY_COUNT ]; then
        echo "✗ SQL Gateway not available after $RETRY_COUNT attempts"
        exit 1
    fi
    [ $((i % 10)) -eq 0 ] && echo "  Attempt $i/$RETRY_COUNT..."
    sleep $RETRY_DELAY
done

# Create session
echo "Creating SQL Gateway session..."
SESSION_RESPONSE=$(curl -sf -X POST "$GATEWAY_URL/v1/sessions" \
    -H "Content-Type: application/json" \
    -d '{
        "properties": {
            "execution.runtime-mode": "streaming",
            "execution.checkpointing.interval": "10s",
            "execution.checkpointing.mode": "EXACTLY_ONCE"
        }
    }')

SESSION_HANDLE=$(echo "$SESSION_RESPONSE" | jq -r '.sessionHandle')
echo "✓ Session created: $SESSION_HANDLE"

# Function to execute SQL statement
execute_sql() {
    local sql="$1"
    local description="$2"

    echo "  $description..."

    RESPONSE=$(curl -sf -X POST "$GATEWAY_URL/v1/sessions/$SESSION_HANDLE/statements" \
        -H "Content-Type: application/json" \
        -d "{\"statement\": $(echo "$sql" | jq -Rs .)}")

    OP_HANDLE=$(echo "$RESPONSE" | jq -r '.operationHandle // empty')

    if [ -n "$OP_HANDLE" ]; then
        # Wait for operation to complete
        for i in $(seq 1 60); do
            STATUS=$(curl -sf "$GATEWAY_URL/v1/sessions/$SESSION_HANDLE/operations/$OP_HANDLE/status" | jq -r '.status')

            if [ "$STATUS" = "FINISHED" ] || [ "$STATUS" = "RUNNING" ]; then
                echo "  ✓ $description complete (status: $STATUS)"
                return 0
            elif [ "$STATUS" = "ERROR" ]; then
                echo "  ✗ $description failed"
                curl -sf "$GATEWAY_URL/v1/sessions/$SESSION_HANDLE/operations/$OP_HANDLE/result/0" | jq '.'
                return 1
            fi

            sleep 2
        done

        echo "  ✗ Operation timeout"
        return 1
    fi

    echo "  ✓ $description submitted"
    return 0
}

# Execute setup SQL
SETUP_FILE="/opt/flink/sql/bronze_${EXCHANGE}_setup.sql"
echo "Executing setup SQL from $SETUP_FILE..."

# Read and execute each statement from setup file
while IFS= read -r line || [ -n "$line" ]; do
    # Skip empty lines and comments
    [[ -z "$line" || "$line" =~ ^[[:space:]]*-- ]] && continue

    # Accumulate multi-line statements
    STMT="$STMT $line"

    # Execute when we hit a semicolon
    if [[ "$line" =~ \;[[:space:]]*$ ]]; then
        execute_sql "$STMT" "Setup statement"
        STMT=""
    fi
done < "$SETUP_FILE"

# Execute job SQL (INSERT statement)
JOB_FILE="/opt/flink/sql/bronze_${EXCHANGE}_job.sql"
echo "Submitting streaming INSERT job from $JOB_FILE..."
JOB_SQL=$(cat "$JOB_FILE")
execute_sql "$JOB_SQL" "Streaming INSERT job"

echo ""
echo "======================================================================"
echo "✓ ${EXCHANGE^^} Bronze Job Submitted Successfully"
echo "======================================================================"
echo "Session Handle: $SESSION_HANDLE"
echo "Monitor at: http://localhost:8082"
echo "======================================================================"
echo ""

# Keep container alive to maintain session
echo "Keeping container alive to maintain SQL Gateway session..."
while true; do
    sleep 60
    # Check if session is still valid
    if ! curl -sf "$GATEWAY_URL/v1/sessions/$SESSION_HANDLE" > /dev/null 2>&1; then
        echo "Warning: Session may have expired"
    fi
done
