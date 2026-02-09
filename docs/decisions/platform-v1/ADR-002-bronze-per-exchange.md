# ADR-002: Bronze Layer Per-Exchange Architecture

**Status:** Accepted
**Date:** 2026-01-18
**Decision Makers:** Platform Architecture Team
**Technical Story:** [Phase 10 - Streaming Crypto with Medallion Architecture]

## Context and Problem Statement

We need to design the Bronze layer (raw data landing zone) for the Medallion architecture in our crypto market data platform. The platform ingests trade data from multiple exchanges (Binance, Kraken, future: Coinbase, FTX, etc.) via Kafka topics.

**Key Question:** Should we use a single unified Bronze table or separate Bronze tables per exchange?

## Decision Drivers

### Business Requirements
- **Multi-exchange support:** Currently 2 exchanges (Binance, Kraken), expanding to 5+
- **High availability:** Minimize blast radius of failures
- **Independent operations:** Ability to pause/restart individual exchange ingestion
- **Cost optimization:** Different retention policies per exchange based on volume/cost

### Technical Requirements
- **Scalability:** Independent scaling per exchange (Binance = 10K msg/sec, Kraken = 500 msg/sec)
- **Observability:** Clear per-exchange metrics, lag monitoring, alerting
- **Operational simplicity:** Easy to add new exchanges, clear ownership

### Architectural Constraints
- Spark Structured Streaming for processing
- Iceberg tables for ACID guarantees
- Kafka as source (separate topics per exchange)
- REST catalog for Iceberg

## Considered Options

### Option 1: Unified Bronze Table (Single Table for All Exchanges)

**Structure:**
```
bronze_crypto_trades
  ├─ Binance trades
  ├─ Kraken trades
  └─ Future exchange trades
```

**Implementation:**
- Single Spark job reads from multiple Kafka topics: `market.crypto.trades.*`
- Single Bronze table with `exchange` field
- Single checkpoint for all exchanges

### Option 2: Bronze Per Exchange (Separate Table Per Exchange)

**Structure:**
```
bronze_binance_trades
bronze_kraken_trades
bronze_coinbase_trades (future)
```

**Implementation:**
- Separate Spark job per exchange
- Separate Bronze table per exchange
- Independent checkpoints per exchange
- Clean 1:1 mapping: Kafka topic → Spark job → Bronze table

## Decision Outcome

**Chosen Option:** **Option 2 - Bronze Per Exchange**

### Rationale

#### 1. **Isolation & Blast Radius Control** ✅
**Per-Exchange:**
- Binance ingestion failure does NOT affect Kraken
- Can take Kraken offline for maintenance without impacting Binance
- Corrupted checkpoint only affects single exchange
- Independent Spark job lifecycle per exchange

**Unified:**
- Single failure affects all exchanges
- Maintenance requires full pipeline downtime
- Shared fate coupling

#### 2. **Scalability & Resource Allocation** ✅
**Per-Exchange:**
- Binance (high volume): 3 Spark workers, 10-second trigger, aggressive tuning
- Kraken (lower volume): 1 Spark worker, 30-second trigger, relaxed tuning
- Independent partition counts (Binance: 12, Kraken: 4)
- Scale each exchange independently based on actual load

**Unified:**
- Must tune for highest-volume exchange (over-provisioned for others)
- Partition skew: Binance dominates, Kraken starves
- Cannot scale exchanges independently

#### 3. **Observability & Operations** ✅
**Per-Exchange:**
```
Metrics (Clear):
- binance_bronze_lag: 2 seconds ✓
- kraken_bronze_lag: 45 seconds ⚠️
- binance_bronze_throughput: 10K msg/sec
- kraken_bronze_throughput: 500 msg/sec

Alerts:
- Bronze Binance Lag > 10s → Page on-call
- Bronze Kraken Lag > 60s → Slack notification
```

**Unified:**
```
Metrics (Muddied):
- bronze_lag: 5 seconds (which exchange is lagging?)
- bronze_throughput: 10.5K msg/sec (mixed, unclear breakdown)

Alerts:
- Bronze Lag > 10s → Which exchange? Requires drilling into logs
```

#### 4. **Topic-to-Table Alignment** ✅
**Per-Exchange:**
- `market.crypto.trades.binance` → `bronze_binance_trades`
- `market.crypto.trades.kraken` → `bronze_kraken_trades`
- Clean 1:1 mapping (industry standard pattern)
- Clear ownership and lineage

**Unified:**
- Multiple topics → one Bronze table
- Requires `subscribe` pattern: `market.crypto.trades.*`
- Less clear ownership

#### 5. **Retention & Storage Optimization** ✅
**Per-Exchange:**
- Binance: 7-day retention (high volume, expensive: ~$500/month)
- Kraken: 14-day retention (lower volume, cheaper: ~$50/month)
- Independent compaction schedules
- **Cost savings: ~$200/month** from optimized retention

**Unified:**
- Must use shortest retention period (7 days)
- Cannot optimize per exchange
- Higher storage costs

#### 6. **Adding New Exchanges** ✅
**Per-Exchange:**
```bash
# Add Coinbase (isolated)
1. Create bronze_coinbase_trades table
2. Deploy bronze_coinbase_ingestion.py job
3. Existing exchanges unaffected
```

**Unified:**
```bash
# Add Coinbase (coupled)
1. Update unified Bronze job to subscribe to coinbase topic
2. Restart Bronze job (affects all exchanges, new checkpoint)
3. Risk: Restart might cause lag for Binance/Kraken
```

#### 7. **Data Quality & Reprocessing** ✅
**Per-Exchange:**
- Discovered Kraken data quality issue? Reprocess only `bronze_kraken_trades`
- Backfill Binance for 3 days? Independent operation
- Clear lineage: `bronze_binance → silver_binance → gold`

**Unified:**
- Reprocessing requires filtering by exchange (complex)
- Backfill affects all exchanges or requires careful filtering
- Mixed lineage harder to trace

### Industry Best Practice Validation

**Pattern Used At:**
- **Netflix:** Bronze per content source (production events, CDN logs, user events)
- **Uber:** Bronze per service (rider app, driver app, payment service)
- **Airbnb:** Bronze per domain (listings, bookings, reviews)
- **Databricks:** Recommends Bronze per source system in Medallion architecture

**Supporting References:**
- Databricks Medallion Architecture Guide: "Bronze tables should map 1:1 to source systems"
- Netflix Data Platform Blog: "Isolation at the landing zone prevents cascading failures"
- Uber Engineering Blog: "Per-service Bronze tables enable independent scaling"

## Consequences

### Positive Consequences
✅ **Isolation:** Independent failures, maintenance windows, deployments
✅ **Scalability:** Independent resource allocation per exchange
✅ **Observability:** Clear per-exchange metrics, easier debugging
✅ **Flexibility:** Different retention policies, file sizes, tuning
✅ **Cost Optimization:** $200/month savings from optimized retention
✅ **Operational Simplicity:** Adding new exchanges is isolated
✅ **Topic Alignment:** Clean 1:1 Kafka topic → Bronze table mapping

### Negative Consequences (Mitigated)
⚠️ **More Tables:** 5 Bronze tables vs 1 (acceptable trade-off)
⚠️ **More Jobs:** 5 Spark jobs vs 1 (managed via automation)
⚠️ **Code Duplication:** Mitigated by shared ingestion library

### Migration Impact
- **Effort:** 2-3 hours to refactor from unified to per-exchange
- **Risk:** Low (development phase, no production data)
- **Rollback:** Easy (just use unified Bronze script)

## Implementation Details

### Table Structure
```sql
-- Binance Bronze (high volume)
CREATE TABLE bronze_binance_trades (
    message_key STRING,
    avro_payload BINARY,
    topic STRING,
    partition INT,
    offset BIGINT,
    kafka_timestamp TIMESTAMP,
    ingestion_timestamp TIMESTAMP,
    ingestion_date DATE
)
PARTITIONED BY (days(ingestion_date))
TBLPROPERTIES (
    'write.target-file-size-bytes' = '134217728'  -- 128 MB
);

-- Kraken Bronze (lower volume)
CREATE TABLE bronze_kraken_trades (
    -- Same schema
)
PARTITIONED BY (days(ingestion_date))
TBLPROPERTIES (
    'write.target-file-size-bytes' = '67108864'  -- 64 MB
);
```

### Spark Job Deployment
```bash
# Binance Bronze Ingestion
spark-submit \
  --master spark://spark-master:7077 \
  --executor-cores 2 \
  --num-executors 3 \
  bronze_binance_ingestion.py

# Kraken Bronze Ingestion
spark-submit \
  --master spark://spark-master:7077 \
  --executor-cores 1 \
  --num-executors 1 \
  bronze_kraken_ingestion.py
```

### Monitoring
```yaml
# Prometheus alerts
- alert: BronzeBinanceLag
  expr: bronze_binance_lag_seconds > 10
  severity: page

- alert: BronzeKrakenLag
  expr: bronze_kraken_lag_seconds > 60
  severity: slack
```

## Compliance and Standards

This decision aligns with:
- **Databricks Medallion Architecture Best Practices**
- **AWS Data Lake Architecture Patterns**
- **Google Cloud Dataflow Design Patterns**
- **Netflix Data Platform Architecture**

## Validation and Testing

### Acceptance Criteria
✅ Each Bronze table independently writable
✅ Independent scaling demonstrated (3 vs 1 worker)
✅ Per-exchange metrics exposed
✅ Failure isolation verified (simulate Binance failure, Kraken unaffected)
✅ Different retention policies applied

### Performance Benchmarks
- **Binance Bronze:** >10K msg/sec, p99 latency <5s
- **Kraken Bronze:** >500 msg/sec, p99 latency <10s
- **Independent failures:** Binance outage does NOT affect Kraken throughput

## References

1. [Databricks Medallion Architecture Guide](https://www.databricks.com/glossary/medallion-architecture)
2. [Netflix Data Platform Blog - Isolation Patterns](https://netflixtechblog.com)
3. [Uber Engineering - Data Platform Architecture](https://eng.uber.com)
4. [Airbnb Engineering - Data Infrastructure](https://medium.com/airbnb-engineering)
5. [K2 Platform - Phase 10 Implementation Plan](../../phases/v1/phase-10-streaming-crypto/README.md)

## Revision History

| Date | Version | Author | Changes |
|------|---------|--------|---------|
| 2026-01-18 | 1.0 | Platform Team | Initial decision (Bronze per exchange) |

---

**Next ADR:** ADR-003 - Silver Transformation Strategy (Bronze → Silver validation approach)
