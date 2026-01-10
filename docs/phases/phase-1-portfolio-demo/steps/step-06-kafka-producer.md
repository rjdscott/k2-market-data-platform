# Step 6: Ingestion Layer - Kafka Producer

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 4-6 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 2 (Schemas), Step 5 (Configuration)
- **Blocks**: Step 7 (Batch Loader needs producer)

## Goal
Implement Kafka producer with Avro serialization and Schema Registry integration. Enable exactly-once semantics with idempotent producer and automatic schema validation.

---

## Implementation

### 6.1 Implement Avro Producer

**File**: `src/k2/ingestion/producer.py`

See original plan lines 1210-1374 for complete implementation.

Key features:
- Confluent Kafka producer with Avro serialization
- Schema Registry integration for automatic validation
- Idempotent producer (`enable.idempotence=True`)
- Compression (lz4) and batching for throughput
- Delivery callbacks for monitoring
- Metrics tracking

### 6.2 Test Producer

**Files**:
- `tests/unit/test_producer.py` - Mocked unit tests
- `tests/integration/test_producer.py` - Real Kafka tests

---

## Validation Checklist

- [ ] Producer implementation created (`src/k2/ingestion/producer.py`)
- [ ] Unit tests pass: `pytest tests/unit/test_producer.py -v`
- [ ] Integration tests pass: `pytest tests/integration/test_producer.py -v`
- [ ] Messages visible in Kafka UI: http://localhost:8080
- [ ] Messages are Avro-serialized (binary, not JSON)
- [ ] Schema Registry shows correct schema version
- [ ] Idempotent producer enabled
- [ ] Metrics tracked (messages produced, delivery failures)

---

## Rollback Procedure

1. **Remove producer code**:
   ```bash
   rm src/k2/ingestion/producer.py
   rm tests/unit/test_producer.py
   rm tests/integration/test_producer.py
   ```

2. **Purge test messages from Kafka** (optional):
   ```bash
   docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --delete --topic market.trades.raw
   # Recreate topic
   python scripts/init_infra.py
   ```

---

## Notes & Decisions

### Decisions Made
- **Idempotent producer**: Prevents duplicates even with retries
- **LZ4 compression**: Best balance of compression ratio and CPU
- **Batch size 32KB**: Optimizes network utilization

### References
- Confluent Kafka Python: https://docs.confluent.io/kafka-clients/python/current/overview.html
- Avro Serializer: https://docs.confluent.io/platform/current/schema-registry/index.html
