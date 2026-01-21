# Step 06: Architecture Decision Summary Document

**Status**: ⬜ Not Started
**Priority**: 🟡 MEDIUM
**Estimated Time**: 2 hours
**Dependencies**: None (can run in parallel)
**Last Updated**: 2026-01-14

---

## Goal

Create unified reference for "Why did you choose X over Y?" questions with evidence-based rationale.

**Why Important**: Principal engineers probe architectural decisions. Having consolidated rationale with alternatives demonstrates thoughtful decision-making.

---

## Deliverables

1. ✅ `docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md`
2. ✅ One-page summary added to quick reference

---

## Implementation

Create `docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md`:

```markdown
# Architecture Decisions Quick Reference

**Purpose**: Evidence-based answers for "Why X over Y?" questions  
**Last Updated**: 2026-01-14  
**Audience**: Principal/Staff engineers during demo

---

## Technology Choices

### Why Kafka vs Alternatives?

**Decision**: Apache Kafka (KRaft mode, no ZooKeeper)

**Alternatives Considered**:
- **Pulsar**: Smaller ecosystem, less proven at scale, fewer integrations
- **RabbitMQ**: Not optimized for high-throughput streaming, designed for task queues
- **AWS Kinesis**: Vendor lock-in, higher cost ($0.015/GB vs self-hosted), AWS-only

**Rationale**:
- Proven at scale (Netflix processes 8M+ msg/sec on Kafka)
- Strong ecosystem (Kafka Connect, Schema Registry, ksqlDB)
- Open source, portable across clouds (AWS, GCP, Azure, bare-metal)
- Idempotency built-in (enable.idempotence=true)

**Trade-offs**:
- Higher operational burden vs managed service (Kinesis)
- More complex than simple message queue (RabbitMQ)

**Evidence**: Netflix, LinkedIn, Uber all run Kafka at massive scale

---

### Why Iceberg vs Alternatives?

**Decision**: Apache Iceberg for lakehouse storage

**Alternatives Considered**:
- **Delta Lake**: Tighter Databricks coupling, less vendor-neutral
- **Apache Hudi**: Optimized for write-heavy (we're read-heavy analytics)
- **Plain Parquet**: No ACID, no time-travel, no schema evolution

**Rationale**:
- ACID transactions (critical for compliance, no partial writes)
- Time-travel queries (regulatory requirement for audit trails)
- Schema evolution without full table rewrites
- Vendor-neutral (works on MinIO, S3, GCS, Azure Blob)
- Hidden partitioning (users don't specify partition predicates)

**Trade-offs**:
- More complex than plain Parquet files
- Requires metadata catalog (PostgreSQL in our case)

**Evidence**: Apple manages 10M+ Iceberg tables in production

---

### Why DuckDB vs Presto/Trino?

**Decision**: DuckDB for single-node demo, Presto for Phase 5 multi-node

**Alternatives Considered**:
- **Presto/Trino**: Overkill for single-node, adds operational complexity (coordinators, workers)
- **Spark**: JVM overhead, slower cold-start, requires cluster management
- **Raw Parquet + Python**: No query optimizer, manual partition handling

**Rationale**:
- Embedded, zero-ops for single-node (no separate cluster)
- Fast analytical queries (<500ms p99 measured)
- Low memory footprint (<1GB for typical queries)
- Native Iceberg support (reads metadata directly)

**Trade-offs**:
- Single-node only (scales vertically, not horizontally)
- Will migrate to Presto for distributed queries in Phase 5

**Migration Path**: Presto cluster + distributed Iceberg catalog (Phase 5)

**Evidence**: DuckDB achieves 1GB/sec scan rates on Parquet

---

### Why Avro vs Protobuf/JSON?

**Decision**: Apache Avro with Schema Registry

**Alternatives Considered**:
- **Protobuf**: Requires code generation, less dynamic, no registry integration
- **JSON**: 3-5x larger, slower serialization, no schema enforcement
- **MessagePack**: No schema enforcement, binary format without evolution

**Rationale**:
- Native Kafka ecosystem support (Confluent Schema Registry)
- Schema Registry integration (BACKWARD compatibility enforced)
- Compact binary format (2-3x smaller than JSON)
- Dynamic schema resolution (no code generation required)
- Schema evolution built-in (add fields without breaking consumers)

**Trade-offs**:
- Less human-readable than JSON (need tooling to inspect)
- Schema Registry adds operational dependency

**Evidence**: Confluent recommends Avro for Kafka streaming

---

## Design Decisions

### Why L3 Cold Path Positioning?

**Decision**: Position as Research Data Platform, not HFT execution

**Alternatives Considered**:
- **L1 Hot Path (HFT)**: Requires specialized hardware (FPGAs), <10μs latency
- **L2 Warm Path (Risk)**: In-memory, <10ms latency, different use case

**Rationale**:
- Honest about 500ms p99 latency (5,000x slower than HFT, perfectly fine for analytics)
- Clear differentiation from execution and risk management
- Targets quant research, compliance, analytics (not trading)
- Avoids competing in a market we're not built for

**Evidence**: Market segments have different latency requirements
- L1 Execution: <10μs (FPGAs, colocated)
- L2 Risk Management: <10ms (in-memory, CEP)
- L3 Analytics/Compliance: <500ms (columnar storage, batch)

**Reference**: docs/architecture/platform-positioning.md

---

### Partitioning Strategy: Date + Hash(Symbol)

**Decision**: Partition by `exchange_date` + `hash(symbol, 16 buckets)`

**Alternatives Considered**:
- **By symbol only**: Too many partitions (10K+ symbols), small file problem
- **By date only**: No query pruning for symbol filters (scan all data)
- **By hour**: Too granular, partition explosion, metadata overhead

**Rationale**:
- Date partitioning enables time-range queries (most common: "last 7 days")
- Symbol hashing (16 buckets) balances partition count vs pruning
- Symbol-level queries scan 1/16 of data (6.25% vs 100%)
- Date + symbol queries scan 1 date partition + 1/16 symbols (minimal I/O)

**Trade-offs**:
- Multi-symbol queries may hit multiple hash buckets (still acceptable)
- Fixed bucket count (requires repartition to change)

**Evidence**: Iceberg best practices recommend date + hash partitioning for high-cardinality dimensions

---

### 5-Level Degradation Cascade

**Decision**: NORMAL → LIGHT → MODERATE → SEVERE → CRITICAL (0-4)

**Alternatives Considered**:
- **Binary on/off**: Too abrupt, cliff-edge failure
- **3-level**: Insufficient granularity, large jumps between states

**Rationale**:
- Gradual degradation prevents cliff-edge failures
- Each level sheds progressively more load (Tier 3 first, then Tier 2, etc.)
- Hysteresis prevents state flapping (requires sustained improvement to recover)
- Observable via Prometheus metrics (ops team sees state)

**Implementation**: src/k2/common/degradation_manager.py (304 lines, 34 tests)

**Reference**: Phase 3 Step 02 (Graceful Degradation)

---

## Deferred Decisions (Not Over-Engineering)

### Redis Sequence Tracker → Deferred to Multi-Node

**Original Plan**: Redis-backed sequence tracking for multi-node dedup

**Decision**: Keep in-memory dict for single-node

**Rationale**:
- In-memory dict faster (<1μs vs 1-2ms network hop to Redis)
- Current scale (138 msg/sec) is I/O bound, not GIL bound
- Will implement when scaling to multi-node (>100K msg/sec)

**When to revisit**: Phase 5 (multi-node deployment)

**Reference**: docs/TODO.md, Phase 3 DECISIONS.md #004

---

### Bloom Filter Dedup → Deferred to Multi-Node

**Original Plan**: Bloom filter + Redis for 24hr dedup window

**Decision**: In-memory dict sufficient for current scale

**Rationale**:
- Current peak: 10K-50K msg/sec fits in memory (<100MB for 24hr window)
- Bloom false positives require Redis confirmation anyway (2 hops)
- Will implement at scale with distributed state requirement

**When to revisit**: Phase 5 (multi-node with 24hr dedup window)

**Reference**: docs/TODO.md, Phase 3 DECISIONS.md #004

---

## Decision Framework

### When to Add Complexity

1. **Now**: Solving current problem (single-node demo, proven value)
2. **Phase 5**: Solving known next problem (multi-node scale, clear requirement)
3. **Later**: Speculative future problem (defer until real need)

**Philosophy**: Pragmatic engineering
- Build what's needed now
- Design for next scale point
- Defer speculation until evidence

### Examples

**Added Now (Phase 1-3)**:
- Circuit breaker: Real need (consumer lag happens)
- Hybrid queries: Real need (show uncommitted data)
- Cost model: Real need (justify platform investment)

**Deferred to Phase 5**:
- Redis sequence tracker: Not needed until multi-node
- Bloom filters: Not needed at current scale
- Presto cluster: Not needed until multi-node

**Not Planning Yet**:
- Multi-region replication: No requirement
- Real-time alerting: Not in scope for L3 cold path
- Machine learning features: Speculative

---

## Quick Reference Summary (for Step 04)

Add to quick reference:

```markdown
## Architecture Decision Shortcuts

- **Kafka**: Proven scale (Netflix 8M msg/sec), open source, strong ecosystem
- **Iceberg**: ACID + time-travel (compliance), vendor-neutral
- **DuckDB**: Fast embedded analytics, Presto planned for multi-node (Phase 5)
- **Avro**: Kafka native, Schema Registry, compact binary (2-3x smaller than JSON)
- **L3 positioning**: Honest about latency (<500ms), clear target (analytics not execution)
- **Partitioning**: Date + hash(symbol, 16) balances pruning vs file count
- **5-level degradation**: Gradual cascade prevents cliff-edge failures
- **Deferred Redis/Bloom**: Over-engineering for single-node, needed at scale
```

---

**Last Updated**: 2026-01-14
```

---

## Validation

```bash
# Verify file created
ls docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md

# Check has multiple "Why X vs Y" sections
grep -c "Why.*vs" \
  docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md
# Should be >5

# Verify all major decisions covered
grep "Decision:" \
  docs/phases/phase-4-demo-readiness/reference/architecture-decisions-summary.md
```

---

## Success Criteria

**5/5 points** — Architectural Decisions Documented

- [ ] Architecture decisions document created
- [ ] All major technology choices covered (Kafka, Iceberg, DuckDB, Avro)
- [ ] Evidence provided for each decision
- [ ] Trade-offs clearly stated
- [ ] Alternatives considered and rejected with rationale
- [ ] Added to quick reference (shortened version)

---

## Demo Talking Points

> "Let me walk you through some of the key architectural decisions and why
> we made them.
> 
> **Kafka over Pulsar or Kinesis**: Kafka is proven at Netflix scale (8M+ msg/sec),
> open source and portable, with a massive ecosystem. Yes, it's more complex than
> a managed service, but we get full control and cost savings.
> 
> **Iceberg over Delta Lake**: Vendor-neutral was critical. Iceberg works on any
> object store - MinIO, S3, GCS, Azure. Plus we get ACID transactions and
> time-travel queries, which are compliance requirements.
> 
> **DuckDB for single-node, Presto for multi-node**: This is pragmatic engineering.
> DuckDB is zero-ops embedded analytics, perfect for this demo. When we scale to
> multi-node in Phase 5, we'll add a Presto cluster for distributed queries.
> Build what's needed now, design for next scale point.
> 
> **Deferred decisions**: We explicitly chose NOT to add Redis-backed sequence
> tracking or Bloom filters yet. They're over-engineering at current scale
> (138 msg/sec). We'll add them in Phase 5 when we hit multi-node. This is
> disciplined decision-making - not everything needs to be built upfront."

---

**Last Updated**: 2026-01-14
