#!/bin/bash
# Bronze Raw Bytes Migration - Validation Script
#
# This script validates that Bronze tables are storing raw bytes correctly
# after the migration from deserialized schema to raw bytes schema.
#
# Usage: ./scripts/migrations/validate_bronze_raw_bytes.sh

set -e

echo "========================================================================"
echo "Bronze Raw Bytes Migration - Validation"
echo "========================================================================"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check 1: Verify Bronze tables exist with correct schema
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Check 1: Verify Bronze Table Schemas"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for table in bronze_binance_trades bronze_kraken_trades; do
    echo -e "\n${YELLOW}Checking schema for: ${table}${NC}"

    schema=$(docker exec k2-spark-master /opt/spark/bin/spark-sql -e \
        "DESCRIBE iceberg.market_data.${table}" 2>&1)

    if echo "$schema" | grep -q "raw_bytes"; then
        echo -e "${GREEN}✓ ${table} has raw_bytes field${NC}"
    else
        echo -e "${RED}✗ ${table} missing raw_bytes field!${NC}"
        echo "Schema:"
        echo "$schema"
        exit 1
    fi

    # Check for old fields (should NOT exist)
    if echo "$schema" | grep -q "event_type\|channel_id"; then
        echo -e "${RED}✗ ${table} still has old deserialized fields!${NC}"
        echo "Schema:"
        echo "$schema"
        exit 1
    else
        echo -e "${GREEN}✓ ${table} does not have old deserialized fields${NC}"
    fi
done

# Check 2: Verify Bronze Spark jobs are running
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Check 2: Verify Bronze Streaming Jobs"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for job in bronze-binance-stream bronze-kraken-stream; do
    echo -e "\n${YELLOW}Checking job: ${job}${NC}"

    status=$(docker compose ps ${job} --format "{{.State}}" 2>&1)

    if [ "$status" = "running" ]; then
        echo -e "${GREEN}✓ ${job} is running${NC}"
    else
        echo -e "${RED}✗ ${job} is not running (status: ${status})${NC}"
        echo "Check logs: docker logs k2-${job} -f"
        exit 1
    fi
done

# Check 3: Wait for data ingestion
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Check 3: Wait for Data Ingestion (30 seconds)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo -e "${YELLOW}Waiting for Bronze jobs to ingest data...${NC}"
for i in {30..1}; do
    echo -ne "\r  Time remaining: ${i}s "
    sleep 1
done
echo ""

# Check 4: Verify raw_bytes are stored
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Check 4: Verify Raw Bytes Storage"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for table in bronze_binance_trades bronze_kraken_trades; do
    echo -e "\n${YELLOW}Checking data in: ${table}${NC}"

    # Row count
    count=$(docker exec k2-spark-master /opt/spark/bin/spark-sql -e \
        "SELECT COUNT(*) as count FROM iceberg.market_data.${table}" 2>&1 | tail -1)

    echo "  Row count: ${count}"

    if [ "$count" -gt 0 ]; then
        echo -e "${GREEN}✓ ${table} has data${NC}"

        # Check raw_bytes length
        length=$(docker exec k2-spark-master /opt/spark/bin/spark-sql -e \
            "SELECT length(raw_bytes) as len FROM iceberg.market_data.${table} LIMIT 1" 2>&1 | tail -1)

        echo "  raw_bytes length: ${length} bytes"

        if [ "$length" -gt 5 ]; then
            echo -e "${GREEN}✓ raw_bytes has Schema Registry header (5 bytes) + payload${NC}"
        else
            echo -e "${RED}✗ raw_bytes too short (expected >5 bytes)${NC}"
            exit 1
        fi

        # Sample record
        echo "  Sample record:"
        docker exec k2-spark-master /opt/spark/bin/spark-sql -e \
            "SELECT topic, partition, offset, length(raw_bytes) as raw_bytes_length, kafka_timestamp FROM iceberg.market_data.${table} LIMIT 1" 2>&1 | tail -5

    else
        echo -e "${RED}✗ ${table} has no data yet${NC}"
        echo "  Possible causes:"
        echo "    1. Kafka topics empty (check: docker exec k2-kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic market.crypto.trades.binance.raw --max-messages 1)"
        echo "    2. Bronze jobs not producing yet (check logs: docker logs k2-bronze-binance-stream -f)"
        echo "    3. Need to wait longer for ingestion"
    fi
done

# Check 5: Verify Kafka topics have data
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Check 5: Verify Kafka Topics"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

for topic in market.crypto.trades.binance.raw market.crypto.trades.kraken.raw; do
    echo -e "\n${YELLOW}Checking topic: ${topic}${NC}"

    # Check if topic exists
    if docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list 2>&1 | grep -q "^${topic}$"; then
        echo -e "${GREEN}✓ Topic exists${NC}"

        # Check for messages (timeout after 2s)
        if timeout 2s docker exec k2-kafka kafka-console-consumer \
            --bootstrap-server localhost:9092 \
            --topic ${topic} \
            --max-messages 1 \
            --timeout-ms 1000 >/dev/null 2>&1; then
            echo -e "${GREEN}✓ Topic has messages${NC}"
        else
            echo -e "${YELLOW}⚠ Topic exists but no messages found${NC}"
            echo "  Start producer: docker compose up -d binance-producer kraken-producer"
        fi
    else
        echo -e "${RED}✗ Topic does not exist${NC}"
        echo "  Create topic or start producers"
    fi
done

# Summary
echo ""
echo "========================================================================"
echo "Validation Summary"
echo "========================================================================"
echo ""
echo -e "${GREEN}✓ Bronze migration validated successfully!${NC}"
echo ""
echo "Bronze tables are now storing raw bytes with Schema Registry headers."
echo "Next step: Proceed with Silver transformation (Step 11 continuation)"
echo ""
echo "Useful commands:"
echo "  • Check Spark UI: http://localhost:8090"
echo "  • Query Bronze: docker exec k2-spark-master /opt/spark/bin/spark-sql -e \"SELECT * FROM iceberg.market_data.bronze_binance_trades LIMIT 5\""
echo "  • Check logs: docker logs k2-bronze-binance-stream -f"
echo ""
