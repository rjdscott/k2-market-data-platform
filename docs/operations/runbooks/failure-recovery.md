# Phase 5: Failure Recovery — Manual Test Procedures

**Date:** 2026-02-12
**Phase:** 5 (Cold Tier Restructure)
**Priority:** 3
**Engineer:** Staff Data Engineer
**Status:** Ready for Manual Execution

---

## Overview

This document provides manual test procedures for validating exactly-once semantics and failure recovery in the Iceberg offload pipeline. These procedures are INTENTIONALLY MANUAL because:

1. **Safety**: Destructive operations (killing containers) should be controlled and monitored
2. **Observability**: Manual execution allows real-time observation of failure modes
3. **Documentation**: Manual procedures force clear documentation of recovery steps
4. **Production Readiness**: SREs need runbooks, not automated chaos

## Prerequisites

- Phase 5 offload pipeline operational (P1 & P2 validated)
- Docker compose v2 stack running
- ClickHouse with live trade data
- PostgreSQL watermark tracking operational
- Iceberg tables created

---

## Test 1: Network Interruption Recovery

### Objective
Validate offload pipeline recovers gracefully from ClickHouse network interruption mid-read.

### Success Criteria
- [ ] Offload fails gracefully when ClickHouse unavailable
- [ ] Watermark NOT updated on failure
- [ ] Retry succeeds after ClickHouse restored
- [ ] Zero data loss
- [ ] Zero duplicates

### Procedure

#### Setup (5 minutes)

1. **Verify baseline state:**
   ```bash
   # Check ClickHouse row count
   docker exec k2-clickhouse clickhouse-client \
     --query="SELECT COUNT(*) FROM k2.bronze_trades_binance"

   # Check current watermark
   docker exec k2-prefect-db psql -U prefect -d prefect \
     -c "SELECT * FROM offload_watermarks WHERE table_name='bronze_trades_binance' ORDER BY created_at DESC LIMIT 1"

   # Check Iceberg row count
   docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog \
     --conf spark.sql.catalog.demo.type=hadoop \
     --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse \
     -e "SELECT COUNT(*) FROM demo.cold.bronze_trades_binance"
   ```

2. **Insert test data:**
   ```bash
   docker exec k2-clickhouse clickhouse-client --query="
   INSERT INTO k2.bronze_trades_binance
   SELECT
     now() - INTERVAL (number % 60) SECOND as exchange_timestamp,
     9000000 + number as sequence_number,
     'binance' as exchange,
     'BTCUSDT' as symbol,
     true as is_buyer_maker,
     46000.0 as price,
     0.1 as quantity,
     'nettest-' || toString(number) as trade_id,
     now() as inserted_at
   FROM system.numbers
   LIMIT 500
   "
   ```

#### Test Execution (10 minutes)

3. **Start offload in background:**
   ```bash
   # Terminal 1: Start offload
   docker exec k2-spark-iceberg bash -c "
   python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze
   " &

   OFFLOAD_PID=$!
   echo "Offload PID: $OFFLOAD_PID"
   ```

4. **Kill ClickHouse mid-execution:**
   ```bash
   # Wait 2 seconds for read to start
   sleep 2

   # Kill ClickHouse
   docker stop k2-clickhouse
   echo "ClickHouse killed at: $(date)"
   ```

5. **Observe offload failure:**
   ```bash
   # Wait for offload to fail
   wait $OFFLOAD_PID
   EXIT_CODE=$?
   echo "Offload exit code: $EXIT_CODE"  # Should be non-zero
   ```

6. **Verify watermark NOT updated:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect \
     -c "SELECT status, created_at FROM offload_watermarks WHERE table_name='bronze_trades_binance' ORDER BY created_at DESC LIMIT 3"

   # Look for 'failed' or no new entry
   ```

7. **Restart ClickHouse:**
   ```bash
   docker start k2-clickhouse
   echo "ClickHouse restarted at: $(date)"

   # Wait for readiness
   sleep 5
   docker exec k2-clickhouse clickhouse-client --query="SELECT 1"
   ```

8. **Retry offload:**
   ```bash
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze

   echo "Retry completed at: $(date)"
   ```

#### Validation (5 minutes)

9. **Verify data integrity:**
   ```bash
   # Check watermark updated
   docker exec k2-prefect-db psql -U prefect -d prefect \
     -c "SELECT * FROM offload_watermarks WHERE table_name='bronze_trades_binance' ORDER BY created_at DESC LIMIT 1"

   # Verify row counts match
   CH_COUNT=$(docker exec k2-clickhouse clickhouse-client --query="SELECT COUNT(*) FROM k2.bronze_trades_binance")
   ICE_COUNT=$(docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog \
     --conf spark.sql.catalog.demo.type=hadoop \
     --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse \
     -e "SELECT COUNT(*) FROM demo.cold.bronze_trades_binance" | grep -oE '[0-9]+' | head -1)

   echo "ClickHouse: $CH_COUNT"
   echo "Iceberg: $ICE_COUNT"

   # Check for duplicates in Iceberg
   docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog \
     --conf spark.sql.catalog.demo.type=hadoop \
     --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse \
     -e "SELECT sequence_number, COUNT(*) as cnt FROM demo.cold.bronze_trades_binance GROUP BY sequence_number HAVING cnt > 1"

   # Should return empty (no duplicates)
   ```

### Expected Results

- ✅ First offload fails with network error
- ✅ Watermark shows 'failed' status or no update
- ✅ Retry succeeds after ClickHouse restored
- ✅ All 500 test rows in Iceberg
- ✅ Zero duplicates

### Troubleshooting

**If retry fails:**
- Check ClickHouse logs: `docker logs k2-clickhouse | tail -50`
- Verify ClickHouse is fully ready: `docker exec k2-clickhouse clickhouse-client --query="SELECT 1"`
- Check Spark logs: `docker logs k2-spark-iceberg | tail -50`

**If duplicates found:**
- This indicates watermark was incorrectly updated during failure
- Check watermark timestamps vs actual offload completion times
- Review watermark update logic in `watermark_pg.py`

---

## Test 2: Spark Crash Recovery

### Objective
Validate no partial data written to Iceberg when Spark job crashes mid-write.

### Success Criteria
- [ ] Spark crash leaves NO partial data in Iceberg
- [ ] Watermark NOT updated
- [ ] Retry from same watermark succeeds
- [ ] Iceberg atomic commits proven

### Procedure

#### Setup (2 minutes)

1. **Get baseline:**
   ```bash
   ICE_COUNT_BEFORE=$(docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog \
     --conf spark.sql.catalog.demo.type=hadoop \
     --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse \
     -e "SELECT COUNT(*) FROM demo.cold.bronze_trades_binance" | grep -oE '[0-9]+' | head -1)

   echo "Iceberg rows before: $ICE_COUNT_BEFORE"
   ```

2. **Insert large test batch:**
   ```bash
   docker exec k2-clickhouse clickhouse-client --query="
   INSERT INTO k2.bronze_trades_binance
   SELECT
     now() - INTERVAL (number % 60) SECOND as exchange_timestamp,
     9100000 + number as sequence_number,
     'binance' as exchange,
     'ETHUSDT' as symbol,
     false as is_buyer_maker,
     3000.0 as price,
     1.0 as quantity,
     'crashtest-' || toString(number) as trade_id,
     now() as inserted_at
   FROM system.numbers
   LIMIT 10000
   "
   ```

#### Test Execution (5 minutes)

3. **Start offload:**
   ```bash
   docker exec k2-spark-iceberg bash -c "
   python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze
   " &

   OFFLOAD_PID=$!
   ```

4. **Kill Spark mid-write:**
   ```bash
   # Wait for job to start writing (3-5 seconds)
   sleep 4

   # Kill the offload process
   kill -9 $OFFLOAD_PID
   echo "Spark killed at: $(date)"
   ```

#### Validation (5 minutes)

5. **Check for partial data:**
   ```bash
   ICE_COUNT_AFTER_KILL=$(docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog \
     --conf spark.sql.catalog.demo.type=hadoop \
     --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse \
     -e "SELECT COUNT(*) FROM demo.cold.bronze_trades_binance" | grep -oE '[0-9]+' | head -1)

   echo "Iceberg rows after kill: $ICE_COUNT_AFTER_KILL"
   echo "Difference: $((ICE_COUNT_AFTER_KILL - ICE_COUNT_BEFORE))"

   # Should be 0 (no partial commit) or 10000 (full commit before kill)
   ```

6. **Verify watermark:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect \
     -c "SELECT status, created_at FROM offload_watermarks WHERE table_name='bronze_trades_binance' ORDER BY created_at DESC LIMIT 2"
   ```

7. **Run full offload:**
   ```bash
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze
   ```

8. **Verify final state:**
   ```bash
   ICE_COUNT_FINAL=$(docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog \
     --conf spark.sql.catalog.demo.type=hadoop \
     --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse \
     -e "SELECT COUNT(*) FROM demo.cold.bronze_trades_binance" | grep -oE '[0-9]+' | head -1)

   echo "Iceberg rows final: $ICE_COUNT_FINAL"
   echo "Expected: $((ICE_COUNT_BEFORE + 10000))"
   ```

### Expected Results

- ✅ After kill: No partial rows (diff = 0) OR full batch committed (diff = 10000)
- ✅ Watermark not updated if kill happened mid-write
- ✅ Final count = initial + 10000 (all data present)
- ✅ Zero duplicates

---

## Test 3: Watermark Corruption Recovery

### Objective
Validate offload handles corrupted watermark gracefully.

### Success Criteria
- [ ] Corrupted watermark detected
- [ ] System recovers (either full scan or fails safely)
- [ ] No data loss
- [ ] Clear error messages

### Procedure

1. **Backup current watermark:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect \
     -c "COPY (SELECT * FROM offload_watermarks WHERE table_name='bronze_trades_binance' ORDER BY created_at DESC LIMIT 1) TO STDOUT" > /tmp/watermark_backup.csv
   ```

2. **Corrupt watermark:**
   ```bash
   docker exec k2-prefect-db psql -U prefect -d prefect \
     -c "INSERT INTO offload_watermarks (table_name, max_timestamp, max_sequence_number, status, created_at) VALUES ('bronze_trades_binance', NULL, NULL, 'corrupted', NOW())"
   ```

3. **Run offload:**
   ```bash
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze 2>&1 | tee /tmp/corrupt_watermark_test.log
   ```

4. **Observe behavior:**
   - Does it fail with clear error?
   - Does it perform full table scan?
   - Does it skip the corrupted watermark?

5. **Restore watermark if needed:**
   ```bash
   # Restore from backup
   docker exec -i k2-prefect-db psql -U prefect -d prefect \
     -c "DELETE FROM offload_watermarks WHERE status='corrupted'"
   ```

### Expected Results

- ✅ Clear error message about corrupted watermark
- ✅ Job either fails safely OR performs full scan with warning
- ✅ No data loss
- ✅ SRE can diagnose and fix from logs

---

## Test 4: Duplicate Run Prevention

### Objective
Verify running same offload twice produces zero duplicates.

### Success Criteria
- [x] First run processes new data
- [x] Second run processes zero rows (idempotent)
- [x] Zero duplicates in Iceberg
- [x] Watermark unchanged on second run

### Procedure

1. **Insert test data:**
   ```bash
   docker exec k2-clickhouse clickhouse-client --query="
   INSERT INTO k2.bronze_trades_binance
   SELECT
     now() - INTERVAL (number % 30) SECOND as exchange_timestamp,
     9200000 + number as sequence_number,
     'binance' as exchange,
     'BTCUSDT' as symbol,
     true as is_buyer_maker,
     47000.0 as price,
     0.05 as quantity,
     'duptest-' || toString(number) as trade_id,
     now() as inserted_at
   FROM system.numbers
   LIMIT 200
   "
   ```

2. **Run offload (first time):**
   ```bash
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze 2>&1 | tee /tmp/dup_test_run1.log

   # Note rows written
   ```

3. **Run offload (second time):**
   ```bash
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze 2>&1 | tee /tmp/dup_test_run2.log

   # Should write 0 rows
   ```

4. **Check for duplicates:**
   ```bash
   docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog \
     --conf spark.sql.catalog.demo.type=hadoop \
     --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse \
     -e "SELECT sequence_number, COUNT(*) as cnt FROM demo.cold.bronze_trades_binance WHERE trade_id LIKE 'duptest-%' GROUP BY sequence_number HAVING cnt > 1"

   # Should return empty
   ```

### Expected Results

- ✅ Run 1: 200 rows written
- ✅ Run 2: 0 rows written (watermark prevents re-read)
- ✅ Zero duplicates found
- ✅ Watermark identical after both runs

---

## Test 5: Late-Arriving Data

### Objective
Validate watermark prevents backfilling late-arriving data.

### Success Criteria
- [ ] Late data (before watermark) is NOT read
- [ ] Watermark behavior documented
- [ ] Trade-off understood (no backfill vs exactly-once)

### Procedure

1. **Run baseline offload:**
   ```bash
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze
   ```

2. **Get watermark timestamp:**
   ```bash
   WATERMARK=$(docker exec k2-prefect-db psql -U prefect -d prefect -t \
     -c "SELECT max_timestamp FROM offload_watermarks WHERE table_name='bronze_trades_binance' ORDER BY created_at DESC LIMIT 1")

   echo "Current watermark: $WATERMARK"
   ```

3. **Insert late-arriving data:**
   ```bash
   # Use timestamp 1 hour BEFORE watermark
   docker exec k2-clickhouse clickhouse-client --query="
   INSERT INTO k2.bronze_trades_binance
   SELECT
     now() - INTERVAL 2 HOUR as exchange_timestamp,
     9300000 + number as sequence_number,
     'binance' as exchange,
     'BTCUSDT' as symbol,
     false as is_buyer_maker,
     48000.0 as price,
     0.2 as quantity,
     'late-' || toString(number) as trade_id,
     now() as inserted_at
   FROM system.numbers
   LIMIT 50
   "
   ```

4. **Run offload again:**
   ```bash
   docker exec k2-spark-iceberg python3 /home/iceberg/offload/offload_generic.py \
     --source-table bronze_trades_binance \
     --target-table cold.bronze_trades_binance \
     --timestamp-col exchange_timestamp \
     --sequence-col sequence_number \
     --layer bronze 2>&1 | tee /tmp/late_data_test.log

   # Should read 0 rows (late data before watermark)
   ```

5. **Verify late data not in Iceberg:**
   ```bash
   docker exec k2-spark-iceberg spark-sql \
     --conf spark.sql.catalog.demo=org.apache.iceberg.spark.SparkCatalog \
     --conf spark.sql.catalog.demo.type=hadoop \
     --conf spark.sql.catalog.demo.warehouse=/home/iceberg/warehouse \
     -e "SELECT COUNT(*) FROM demo.cold.bronze_trades_binance WHERE trade_id LIKE 'late-%'"

   # Should return 0
   ```

### Expected Results

- ✅ Late data NOT read (watermark prevents backfill)
- ✅ 0 rows written on second offload
- ✅ Late data remains in ClickHouse only
- ⚠️  **Trade-off documented**: Exactly-once semantics prevent backfill

### Design Note

This is EXPECTED BEHAVIOR. Watermark-based exactly-once semantics intentionally prevent backfilling to avoid duplicates. If late-arriving data is critical, options are:

1. Increase buffer window (currently 5 minutes)
2. Manual backfill with watermark reset (operational procedure)
3. Accept ClickHouse-only retention for late data

---

## Summary Checklist

### Critical Tests (Must Pass)
- [ ] Test 1: Network Interruption Recovery
- [ ] Test 2: Spark Crash Recovery (no partial data)
- [ ] Test 4: Duplicate Run Prevention (idempotency)

### Important Tests (Should Pass)
- [ ] Test 3: Watermark Corruption Recovery
- [ ] Test 5: Late-Arriving Data Handling

### Documentation
- [ ] All test results captured in `docs/testing/failure-recovery-report-2026-02-12.md`
- [ ] Failure modes documented
- [ ] Recovery procedures validated
- [ ] SRE runbooks updated with learnings

---

**Last Updated:** 2026-02-12
**Next Review:** After P3 completion
**Related Docs:**
- [Production Validation Report](production-validation-report-2026-02-12.md)
- [Multi-Table Test Report](multi-table-offload-report-2026-02-12.md)
- [Phase 5 Progress](../phases/v2/phase-5-cold-tier-restructure/PROGRESS.md)

---

*This manual test guide follows staff-level engineering practices: safety first, clear procedures, expected results, troubleshooting guidance.*
