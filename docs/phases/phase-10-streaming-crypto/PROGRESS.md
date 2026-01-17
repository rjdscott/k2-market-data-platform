# Phase 10 Progress Tracker

**Last Updated**: 2026-01-18
**Overall Progress**: 0/16 steps complete (0%)
**Status**: ⬜ Not Started

---

## Milestone Progress

| Milestone | Steps | Complete | Status | Progress |
|-----------|-------|----------|--------|----------|
| 1. Complete Cleanup | 01-03 | 0/3 | ⬜ | 0% |
| 2. Fresh Schema Design | 04-05 | 0/2 | ⬜ | 0% |
| 3. Spark Cluster Setup | 06 | 0/1 | ⬜ | 0% |
| 4. Medallion Tables | 07-09 | 0/3 | ⬜ | 0% |
| 5. Spark Streaming Jobs | 10-12 | 0/3 | ⬜ | 0% |
| 6. Kraken Integration | 13-14 | 0/2 | ⬜ | 0% |
| 7. Testing & Validation | 15-16 | 0/2 | ⬜ | 0% |

**Total**: 0/16 steps complete (0%)

---

## Detailed Step Progress

### Milestone 1: Complete Cleanup (0% complete)

#### Step 01: Remove ASX Code and Data ⬜
- **Status**: Not Started
- **Estimated**: 8 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Zero occurrences of "ASX" in src/, tests/, config/, scripts/
- [ ] batch_loader.py deleted
- [ ] All ASX sample data removed
- [ ] Kafka topics.yaml has no equity sections
- [ ] All tests pass after removal

---

#### Step 02: Remove V1 and Equity Schemas ⬜
- **Status**: Not Started
- **Estimated**: 6 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] V1 `.avsc` files deleted from src/k2/schemas/
- [ ] Schema Registry has zero subjects with "v1", "equity", or "asx"
- [ ] build_trade_v1() and build_quote_v1() removed
- [ ] All unit tests pass

---

#### Step 03: Drop Existing Iceberg Tables ⬜
- **Status**: Not Started
- **Estimated**: 2 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] All v1 and v2 tables dropped from Iceberg catalog
- [ ] SHOW TABLES IN market_data; returns empty
- [ ] MinIO warehouse/market_data/ directory empty
- [ ] Fresh start confirmed

---

### Milestone 2: Fresh Schema Design (0% complete)

#### Step 04: Design Crypto-Only V3 Schema ⬜
- **Status**: Not Started
- **Estimated**: 8 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] V3 schema file created: src/k2/schemas/trade_v3.avsc
- [ ] Schema validates with avro-tools
- [ ] Field count: 12 fields (crypto-optimized)
- [ ] Exchange enum: Only BINANCE, KRAKEN
- [ ] Side enum: Only BUY, SELL
- [ ] Decimal precision: 18,8

---

#### Step 05: Register V3 Schema in Schema Registry ⬜
- **Status**: Not Started
- **Estimated**: 4 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Schema registered in Schema Registry
- [ ] Producer can serialize with v3
- [ ] Consumer can deserialize v3
- [ ] Compatibility mode: NONE
- [ ] Test roundtrip successful

---

### Milestone 3: Spark Cluster Setup (0% complete)

#### Step 06: Spark Infrastructure Setup ⬜
- **Status**: Not Started
- **Estimated**: 12 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Spark master container running
- [ ] 2 workers registered (visible in Web UI)
- [ ] Test job completes successfully
- [ ] Spark Web UI accessible: http://localhost:8080
- [ ] Worker resources: 2 cores, 3GB each
- [ ] PySpark installed and importable

---

### Milestone 4: Medallion Tables (0% complete)

#### Step 07: Bronze Table Creation ⬜
- **Status**: Not Started
- **Estimated**: 6 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Bronze table created: bronze_crypto_trades
- [ ] Schema has 8 fields
- [ ] Partitioning: days(ingestion_date)
- [ ] Compression: Zstd
- [ ] Format version: Iceberg v2
- [ ] MinIO storage exists

---

#### Step 08: Silver Tables Creation ⬜
- **Status**: Not Started
- **Estimated**: 8 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Two Silver tables created
- [ ] Schema: 13 fields matching v3
- [ ] Decimal precision: 18,8
- [ ] Partitioning: days(exchange_date)
- [ ] Compression: Zstd
- [ ] Table metadata exists

---

#### Step 09: Gold Table Creation ⬜
- **Status**: Not Started
- **Estimated**: 6 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Gold table created: gold_crypto_trades
- [ ] Schema: 14 fields (v3 + derived)
- [ ] Partitioning: Hourly (exchange_date, exchange_hour)
- [ ] Sorting: (timestamp, trade_id)
- [ ] Compression: Zstd
- [ ] Index: message_id indexed

---

### Milestone 5: Spark Streaming Jobs (0% complete)

#### Step 10: Bronze Ingestion Job ⬜
- **Status**: Not Started
- **Estimated**: 20 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Job starts successfully in Spark UI
- [ ] Reads from Kafka: both topics
- [ ] Writes to Bronze table
- [ ] Checkpoint works: directory exists
- [ ] Latency: <10 seconds
- [ ] Throughput: 10K msg/sec
- [ ] Recovery: Restarts from checkpoint

---

#### Step 11: Silver Transformation Job ⬜
- **Status**: Not Started
- **Estimated**: 24 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Job starts successfully
- [ ] Reads from Bronze: Deserializes Avro
- [ ] Validation works: Checks prices, timestamps
- [ ] Writes to Silver: Both exchange tables
- [ ] DLQ works: Invalid records captured
- [ ] Latency: <30 seconds
- [ ] Checkpoints exist
- [ ] Recovery works

---

#### Step 12: Gold Aggregation Job ⬜
- **Status**: Not Started
- **Estimated**: 16 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Job starts successfully
- [ ] Reads from Silver: Both tables
- [ ] Union works: Both exchanges combined
- [ ] Deduplication works: No duplicate message_ids
- [ ] Derived fields: exchange_date, exchange_hour populated
- [ ] Partitioning: Hourly partitions created
- [ ] Latency: <60 seconds
- [ ] Recovery works

---

### Milestone 6: Kraken Integration (0% complete)

#### Step 13: Kraken WebSocket Client ⬜
- **Status**: Not Started
- **Estimated**: 16 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] File created: kraken_client.py (~700 lines)
- [ ] Config added: KrakenConfig
- [ ] Kafka topic created: market.crypto.trades.kraken
- [ ] Symbol parsing: Handles BTC/USD, ETH/USD
- [ ] V3 conversion: All fields populated
- [ ] Vendor data preserved
- [ ] Resilience: All 6 patterns implemented
- [ ] Unit tests: 20+ tests
- [ ] Integration test: Live connect works

---

#### Step 14: Kraken Integration Validation ⬜
- **Status**: Not Started
- **Estimated**: 8 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Kraken client running
- [ ] Kafka topic populated
- [ ] Bronze has Kraken trades
- [ ] Silver has Kraken trades
- [ ] Gold has Kraken trades
- [ ] Both exchanges unified in Gold
- [ ] No duplicates in deduplication query

---

### Milestone 7: Testing & Validation (0% complete)

#### Step 15: E2E Pipeline Testing ⬜
- **Status**: Not Started
- **Estimated**: 12 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] All E2E tests pass
- [ ] Latency: p99 <5 minutes
- [ ] Query perf: p99 <500ms
- [ ] Data quality: DLQ captures failures
- [ ] Deduplication: No duplicates
- [ ] Checkpoint recovery: No data loss

---

#### Step 16: Performance Validation ⬜
- **Status**: Not Started
- **Estimated**: 8 hours
- **Actual**: -
- **Started**: -
- **Completed**: -
- **Notes**: -

**Acceptance Criteria**:
- [ ] Bronze throughput: ≥10K msg/sec
- [ ] Silver latency: p99 <30s
- [ ] Gold query: p99 <500ms
- [ ] Checkpoint recovery: <60s
- [ ] Memory stable: <4GB per worker
- [ ] No errors: Zero failed batches

---

## Timeline

**Start Date**: TBD
**Target Completion**: TBD (20 days from start)

### Week 1: Cleanup + Schema Design
- Days 1-2: Steps 01-03 (ASX/Equity removal)
- Days 3-4: Steps 04-06 (Schema + Spark)
- Day 5: Steps 07-09 (Medallion tables)

### Week 2-3: Spark Streaming
- Days 6-8: Step 10 (Bronze job)
- Days 9-12: Step 11 (Silver job)
- Days 13-15: Step 12 (Gold job)

### Week 3-4: Kraken + Validation
- Days 16-18: Steps 13-14 (Kraken)
- Days 19-20: Steps 15-16 (Testing)

---

## Blockers & Issues

**Current Blockers**: None (not started)

**Resolved Issues**: None

**Known Risks**:
- Spark-Iceberg compatibility (mitigation: test early)
- Checkpoint corruption (mitigation: daily backups)
- Memory issues (mitigation: start small, tune)

---

**For implementation details**, see [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md).
**For current status**, see [STATUS.md](./STATUS.md).
