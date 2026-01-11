# Design Documentation

**Last Updated**: 2026-01-10
**Stability**: Medium - evolves with system complexity
**Target Audience**: Senior Engineers, Implementation Team

This directory contains detailed component-level design decisions, interfaces, and trade-offs.

---

## Overview

Design documentation bridges architecture (the "why") and implementation (the "how"). Each component has detailed design docs explaining:
- Component responsibilities
- Interface contracts
- Data flow
- Trade-offs and limitations
- Scaling strategies

---

## Key Documents

### [Query Architecture](./query-architecture.md)
How queries are routed, optimized, and executed.

**Topics**:
- Query routing logic (real-time vs historical vs hybrid)
- Three-layer caching strategy (Redis L1, memory L2, precomputed L3)
- Partition pruning and predicate pushdown
- Resource limits (concurrency, timeouts, memory)
- Performance SLOs (p50 < 1s, p99 < 5s)

**When to read**: Implementing query features, optimizing performance

### Storage Layer Design
Iceberg table design, partitioning strategy, and ACID semantics.

**Topics**:
- Daily partitioning by exchange_timestamp
- Sort order optimization (symbol, exchange_timestamp)
- Schema evolution strategy
- Compaction and maintenance procedures
- Partition evolution without data rewrite

**When to read**: Working with Iceberg tables, schema changes

### Streaming Layer Design
Kafka topic configuration, serialization, and delivery semantics.

**Topics**:
- Topic naming conventions
- Partitioning by symbol (preserves ordering)
- Avro serialization with Schema Registry
- At-least-once delivery with manual commit
- Consumer group management
- Sequence number tracking

**When to read**: Working with Kafka producers/consumers

### API Layer Design
FastAPI REST interface design and patterns.

**Topics**:
- Endpoint design principles
- Pydantic models for validation
- Error handling strategy
- Rate limiting approach
- OpenAPI documentation generation

**When to read**: Adding API endpoints, client integration

---

## Data Guarantees

This subsection contains critical data quality and consistency documentation.

### [data-guarantees/ordering-guarantees.md](./data-guarantees/ordering-guarantees.md)
**Market Data Ordering Guarantees**

- Per-symbol ordering (Kafka partitioning by symbol)
- Sequence number tracking
- Gap detection and logging
- No cross-symbol ordering guarantees (intentional trade-off)

### [data-guarantees/consistency-model.md](./data-guarantees/consistency-model.md)
**Kafka-Iceberg Consistency Model**

- Eventual consistency between Kafka and Iceberg
- At-least-once delivery semantics
- Idempotent Iceberg writes
- Consumer offset management
- Duplicate handling strategy

### [data-guarantees/data-quality.md](./data-guarantees/data-quality.md)
**Data Quality Metrics and Validation**

- Schema validation (Avro)
- Value range validation
- Outlier detection (price spikes)
- Completeness metrics
- Freshness monitoring

### [data-guarantees/correctness-tradeoffs.md](./data-guarantees/correctness-tradeoffs.md)
**Correctness vs Performance Trade-offs**

- At-least-once vs exactly-once
- Daily vs hourly partitioning
- Sync vs async writes
- Decision framework for choosing

---

## Component Interfaces

### Batch Loader → Kafka
**Interface**: Kafka Producer API
**Protocol**: Avro over Kafka binary protocol
**Contract**: Must register schema before producing

```python
producer.produce(
    topic="market-data.trades",
    key=symbol,  # String
    value=trade_record  # Avro-serialized
)
```

### Kafka → Iceberg Consumer
**Interface**: Kafka Consumer API + PyIceberg
**Protocol**: Avro deserialization → Arrow → Iceberg
**Contract**: Manual commit after successful Iceberg write

```python
# Consume from Kafka
msg = consumer.poll(timeout=1.0)

# Write to Iceberg
writer.write(arrow_batch)

# Commit offset
consumer.commit(msg)
```

### DuckDB → Iceberg
**Interface**: DuckDB Iceberg extension
**Protocol**: SQL queries with iceberg_scan()
**Contract**: Read-only access via S3 secret

```sql
SELECT * FROM iceberg_scan('s3://warehouse/market_data.db/trades')
WHERE date(exchange_timestamp) = '2024-01-01'
```

### FastAPI → DuckDB
**Interface**: DuckDB Python API
**Protocol**: Sync Python driver
**Contract**: Connection pooling, query timeouts

```python
conn = duckdb.connect(database=':memory:', read_only=True)
result = conn.execute(query, timeout=5.0).fetchall()
```

---

## Design Patterns

### Idempotency Pattern
Used in: Batch loader, Iceberg consumer

**Pattern**: Hash-based deduplication
**Implementation**: Track processed keys in state store
**Benefit**: Safe retries on failure

### Circuit Breaker Pattern
Used in: API layer, external service calls

**Pattern**: Fail fast after N consecutive failures
**Implementation**: Track failure count, open circuit after threshold
**Benefit**: Prevent cascading failures

### Graceful Degradation Pattern
Used in: Query engine, API layer

**Pattern**: Return partial results on timeout
**Implementation**: Query with timeout, return what's available
**Benefit**: Better UX than hard failure

---

## Performance Considerations

### Partitioning Strategy
**Current**: Daily partitions by exchange_timestamp
**Rationale**: Common queries are "last N days"
**Trade-off**: Intraday queries scan full day partition

### Sort Order
**Current**: Sorted by (symbol, exchange_timestamp)
**Rationale**: Optimize symbol-specific time-range queries
**Trade-off**: Cross-symbol aggregations slower

### Caching Strategy (Future - Phase 2)
**L1 Cache**: Redis (distributed, 1-hour TTL)
**L2 Cache**: In-memory query results (5-minute TTL)
**L3 Cache**: Precomputed aggregations in Iceberg

---

## Trade-off Register

| Decision | Benefit | Cost | Chosen For |
|----------|---------|------|------------|
| Daily partitioning | Simple, common query pattern | Intraday queries slow | Phase 1 simplicity |
| At-least-once | Simple, no data loss | Potential duplicates | Acceptable for market data |
| DuckDB query engine | Zero ops, sub-second | Single-node limit | Phase 1 demo |
| Manual commit | Exactly-once write semantics | Higher latency | Data correctness |
| Symbol partitioning | Per-symbol ordering | No cross-symbol ordering | Market data characteristic |

---

## Future Enhancements

### Phase 2 (Production Prep)
- [ ] Replace DuckDB with Presto cluster
- [ ] Add Redis caching layer
- [ ] Implement query result pagination
- [ ] Add API authentication and rate limiting
- [ ] Enable exactly-once Kafka semantics

### Phase 3 (Scale)
- [ ] Multi-region replication
- [ ] Materialized views for common queries
- [ ] Advanced query optimization (cost-based)
- [ ] Columnar caching layer
- [ ] Distributed tracing

---

## Related Documentation

- **Architecture**: [../architecture/](../architecture/)
- **Implementation**: [../phases/phase-1-single-node-implementation/](../phases/phase-1-single-node-implementation/)
- **Operations**: [../operations/](../operations/)
- **Testing**: [../testing/](../testing/)

---

**Maintained By**: Engineering Team
**Review Frequency**: Monthly
**Last Review**: 2026-01-10
