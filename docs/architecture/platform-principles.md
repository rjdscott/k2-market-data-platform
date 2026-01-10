# K2 Platform Principles

**Last Updated**: 2026-01-09
**Owners**: Platform Team
**Status**: Living Document

---

## Core Principles

### 1. **Replayable by Default**

Every data pipeline must support arbitrary time-range replay from durable storage.

**Rationale**: Market data has unpredictable downstream dependencies. A quant may need to backtest a strategy against 2-year-old ticks. A compliance audit may require reconstructing trades from last quarter. Without replay, these become multi-week engineering projects.

**Implementation Requirements**:
- All Kafka topics configure retention ≥ 7 days (30 days for critical topics)
- Iceberg snapshots retained for 90 days minimum
- Consumer offsets never auto-committed on business logic failure
- Replay jobs use isolated consumer groups to avoid disrupting real-time processing

**Non-Negotiable**: Platform teams will reject any producer that cannot guarantee message immutability.

---

### 2. **Schema-First, Always**

No data enters the platform without a registered schema.

**Rationale**: Unstructured data creates cascading failures. A missing field breaks 15 downstream consumers. An integer that becomes a float invalidates historical comparisons. Schema Registry is not optional tooling—it's the firewall between chaos and reliability.

**Implementation Requirements**:
- Schema Registry enforces BACKWARD compatibility (no breaking changes)
- Producers fail-fast on schema validation errors (no silent drops)
- Breaking changes require explicit migration plan and 30-day deprecation window
- All schemas version-controlled in git alongside code

**Escape Hatch**: Prototyping can use schema-less topics prefixed `experimental.*`, but these are purged after 7 days and unavailable to production consumers.

---

### 3. **Boring Technology**

Choose proven, well-understood systems over novel solutions.

**Rationale**: Platform stability matters more than résumé-driven development. Kafka has 10+ years of production hardening. Iceberg is the emerging standard for lakehouse (Netflix, Apple, Adobe). DuckDB is single-threaded simplicity. When the trading desk loses $50K/minute due to data lag, you want boring technology with known failure modes.

**Decision Framework**:
- **Tier 1 (No Approval Needed)**: Kafka, Iceberg, PostgreSQL, Prometheus
- **Tier 2 (Platform Lead Approval)**: New language runtime, different message broker, alternative query engine
- **Tier 3 (VP Eng + CTO Approval)**: Ground-up rewrites, vendor lock-in (e.g., Snowflake, Databricks)

**Contraindications**: Do NOT choose boring tech when:
- Performance requirements exceed what's possible (e.g., sub-microsecond latency needs custom C++)
- Regulatory requirements mandate specific technology
- Cost reduction is >50% with acceptable risk

---

### 4. **Degrade Gracefully**

Systems must define their failure modes explicitly.

**Rationale**: "High availability" is meaningless without specifying what breaks first under load. Does latency degrade? Do we drop messages? Spill to disk? Downstream teams need to know whether to retry, ignore, or page on-call.

**Implementation Requirements**:
- Every service documents its degradation hierarchy (see [Latency & Backpressure](./LATENCY_BACKPRESSURE.md))
- Circuit breakers trigger before resource exhaustion
- Dashboards show "health score" (0-100) based on SLOs, not binary up/down
- Runbooks specify recovery procedures for each degradation mode

**Example**:
```
Market Data Consumer - Degradation Sequence:
1. p99 latency 100ms → 500ms (still processing, slower)
2. p99 latency > 1s (drop non-critical symbols, alert on-call)
3. Consumer lag > 1M messages (halt writes to Iceberg, spill to S3)
4. OOM imminent (crash and restart, lose in-flight batch)
```

---

### 5. **Idempotency Over Exactly-Once**

Design for at-least-once delivery with idempotent consumers.

**Rationale**: Exactly-once is a leaky abstraction. Kafka transactions have edge cases. Network partitions cause duplicate delivery. Instead of fighting the distributed systems gods, we design systems where duplicate processing is safe.

**Implementation Requirements**:
- Every message includes a stable, unique ID (exchange sequence number + symbol)
- Iceberg writes use merge-on-read with deduplication by message ID
- Downstream systems handle duplicates via upserts (MERGE vs INSERT)
- Consumers checkpoint offsets only after durable writes complete

**When Exactly-Once IS Required**:
- Financial aggregations (total traded volume must be precise)
- Use Kafka transactions with caution, test partition reassignment scenarios
- Document recovery procedure when transactions fail mid-flight

See [Correctness Trade-offs](./CORRECTNESS_TRADEOFFS.md) for detailed decision tree.

---

### 6. **Observable by Default**

If it's not instrumented, it doesn't exist.

**Rationale**: The worst production incident is the one you can't see. Every service emits structured logs, Prometheus metrics, and traces. Debugging "slow queries" without metrics is archaeology.

**Implementation Requirements**:
- **Metrics**: RED (Rate, Errors, Duration) for every service
  - `kafka_consumer_lag_seconds` (critical alert threshold)
  - `iceberg_write_duration_p99` (latency budget tracking)
  - `query_api_requests_total{status="error"}` (error rate SLO)
- **Logging**: Structured JSON with correlation IDs
  - Every log line includes `request_id`, `user_id`, `symbol` for tracing
  - No `print()` statements in production code
- **Tracing**: Distributed traces for cross-service requests
  - Optional in local dev, mandatory in staging/prod

**Non-Negotiable**: PRs without metrics/logging fail code review.

---

## Platform Guardrails

### What Teams MUST Do

1. **Register schemas** before producing to any topic
2. **Handle consumer lag gracefully** (no unbounded memory growth)
3. **Version all deployments** (git SHA in metrics tags)
4. **Write runbooks** for every service (link in on-call rotation)
5. **Test replay scenarios** (integration test must include time-travel query)

### What Teams MUST NOT Do

1. **Bypass Schema Registry** (no raw byte serialization)
2. **Auto-commit Kafka offsets** (always manual offset management)
3. **Hard-delete data from Iceberg** (use soft deletes with retention policies)
4. **Expose PII without encryption** (audit logs track all access)
5. **Deploy without rollback plan** (canary deployments required for critical paths)

---

## Decision Authority

| Decision Type | Authority | Escalation |
|---------------|-----------|------------|
| Add new Kafka topic | Platform team + requesting team lead | Platform Lead |
| Change schema compatibility mode | Platform Lead | VP Engineering |
| Adopt new storage format (e.g., Hudi) | Platform Lead + Architecture Review | CTO |
| Increase retention policy > 90 days | Platform Lead (cost approval from Finance) | VP Engineering |
| Grant production database access | Platform Lead + Security | CISO |

---

## Evolution Process

This document evolves through RFCs (see [RFC Template](./RFC_TEMPLATE.md)).

**Amendment Process**:
1. Propose change via RFC (open PR to this doc)
2. Platform team reviews + comments (minimum 3 business days)
3. Present at weekly Platform Sync meeting
4. Merge requires 2 approvals (Platform Lead + 1 staff engineer)

**Recent Changes**:
- 2026-01-09: Initial version
- (Future changes logged here with PR link)

---

## Related Documentation

- [Correctness Trade-offs](./CORRECTNESS_TRADEOFFS.md) - When to use exactly-once vs at-least-once
- [Latency & Backpressure Design](./LATENCY_BACKPRESSURE.md) - Performance budgets and degradation
- [Failure & Recovery Runbook](./FAILURE_RECOVERY.md) - Operational procedures
- [RFC Process](./RFC_TEMPLATE.md) - How to propose platform changes