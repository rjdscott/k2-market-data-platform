# Step 8: Ingestion Layer - Kafka Consumer with Iceberg Writer

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 6-8 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 4 (Iceberg Writer), Step 6 (schemas for deserialization)
- **Blocks**: Step 9 (Query Engine needs data in Iceberg)

## Goal
Consume from Kafka and write to Iceberg with sequence tracking. Bridge streaming layer (Kafka) and lakehouse (Iceberg) with at-least-once delivery guarantees.

---

## Implementation

### 8.1 Implement Consumer

**File**: `src/k2/ingestion/consumer.py`

See original plan lines 1683-1864 for complete implementation.

Key features:
- Kafka consumer with Avro deserialization
- Batch processing (configurable batch size)
- Manual commit after successful Iceberg write
- Sequence gap detection and logging
- Graceful shutdown handling

### 8.2 Create CLI Command

**File**: Update `src/k2/ingestion/cli.py`

Command: `k2-ingest consume --topic market.trades.raw --max-messages 1000`

### 8.3 Test Consumer

**File**: `tests/integration/test_consumer.py`

---

## Validation Checklist

- [ ] Consumer implementation created (`src/k2/ingestion/consumer.py`)
- [ ] CLI command works: `k2-ingest consume`
- [ ] Integration tests pass
- [ ] Messages consumed from Kafka
- [ ] Data written to Iceberg successfully
- [ ] Manual commit works (no data loss on crash)
- [ ] Sequence gaps detected and logged
- [ ] Consumer lag near zero after consumption
- [ ] Metrics tracked (messages consumed, write errors)

---

## Rollback Procedure

1. **Stop consumer**:
   ```bash
   # Kill any running consumer processes
   pkill -f "k2-ingest consume"
   ```

2. **Reset consumer group offset** (optional):
   ```bash
   docker exec k2-kafka kafka-consumer-groups --bootstrap-server localhost:9092 --group k2-iceberg-writer --reset-offsets --to-earliest --topic market.trades.raw --execute
   ```

3. **Remove code files**:
   ```bash
   # Remove consumer (keep producer and batch loader)
   git checkout src/k2/ingestion/consumer.py
   rm tests/integration/test_consumer.py
   ```

4. **Truncate Iceberg table** (if needed):
   ```python
   from k2.storage.catalog import IcebergCatalogManager
   catalog = IcebergCatalogManager()
   table = catalog.catalog.load_table("market_data.trades")
   table.delete(delete_filter="true")  # Delete all data
   ```

---

## Notes & Decisions

### Decisions Made
- **Manual commit strategy**: Commit only after successful Iceberg write
  - Prevents data loss at cost of potential duplicate processing
  - Iceberg handles duplicates via idempotent writes

- **Batch size**: 1000 records default
  - Balances throughput vs memory usage
  - Configurable per environment

### Performance Considerations
- Batch writes significantly faster than individual writes
- Manual commit adds latency but ensures durability
- Sequence tracking adds minimal overhead

### References
- Confluent Kafka Consumer: https://docs.confluent.io/kafka-clients/python/current/overview.html#consumer
- Consumer offset management: https://docs.confluent.io/platform/current/clients/consumer.html
