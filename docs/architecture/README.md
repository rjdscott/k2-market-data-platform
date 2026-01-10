# Architecture Documentation

**Last Updated**: 2026-01-10
**Stability**: High - changes require RFC and team review
**Target Audience**: Principal/Staff Engineers, Architects

This directory contains permanent architectural decisions that transcend individual implementation phases.

---

## Overview

The K2 Market Data Platform is a lakehouse-based streaming analytics platform designed for:
- **Real-time ingestion** of market data via Apache Kafka
- **ACID storage** in Apache Iceberg lakehouse
- **Sub-second queries** using DuckDB (Phase 1) with path to Presto/Trino
- **Observability** via Prometheus + Grafana

**Architecture Philosophy**: Pragmatic over perfect, boring technology, graceful degradation

---

## Key Documents

### [Platform Principles](./platform-principles.md)
The "constitution" of the K2 platform. Core operational principles:
1. **Replayable by Default** - All data transformations can be re-executed
2. **Schema-First** - Schemas are contracts, registered before use
3. **Boring Technology** - Proven tools over bleeding-edge
4. **Graceful Degradation** - Degrade functionality, never crash
5. **Idempotency** - Operations can be safely retried
6. **Observable by Default** - Every component emits metrics

**When to update**: Adding/changing core principles (requires RFC)

### Technology Decisions
Why we chose our tech stack:
- **Kafka**: Industry-standard streaming, proven at scale
- **Iceberg**: ACID lakehouse with time-travel and schema evolution
- **DuckDB**: Embedded analytics for Phase 1 (migration path to Presto)
- **FastAPI**: Modern Python framework with auto-documentation
- **Prometheus + Grafana**: Open-source observability standard

**Decision Log**: See [Phase 1 DECISIONS.md](../phases/phase-1-portfolio-demo/DECISIONS.md)

### [Alternative Architectures](./alternatives.md)
Architectures we considered and why we rejected them:
- Spark-based lakehouse (too heavy for Phase 1)
- Delta Lake instead of Iceberg (vendor lock-in concerns)
- Kafka Streams instead of consumer (flexibility trade-off)

---

## System Design

### High-Level Architecture

```
┌─────────────────┐
│   CSV Files     │
│  (Sample Data)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Batch Loader   │
│   (Python)      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│  Apache Kafka   │◄─────│ Schema Registry  │
│  (Streaming)    │      │  (Avro Schemas)  │
└────────┬────────┘      └──────────────────┘
         │
         ▼
┌─────────────────┐
│ Kafka Consumer  │
│ (Sequence Track)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Apache Iceberg  │
│  (Lakehouse)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│    DuckDB       │      │   FastAPI        │
│ (Query Engine)  │◄─────│  (REST API)      │
└────────┬────────┘      └──────────────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│   Prometheus    │◄─────│    Grafana       │
│   (Metrics)     │      │  (Dashboards)    │
└─────────────────┘      └──────────────────┘
```

### Component Layers

1. **Ingestion Layer**: CSV → Kafka (Avro serialization)
2. **Storage Layer**: Kafka → Iceberg (ACID writes with partitioning)
3. **Query Layer**: DuckDB scanning Iceberg (predicate pushdown)
4. **API Layer**: FastAPI exposing REST endpoints
5. **Observability Layer**: Prometheus + Grafana

---

## Architectural Principles in Practice

### Replayability
- All Kafka messages timestamped
- Iceberg supports time-travel queries
- Batch loader is idempotent
- Consumer tracks sequence numbers

### Schema-First
- Avro schemas registered before producing
- Schema Registry provides evolution support
- Iceberg schema matches Avro schema
- API models use Pydantic for validation

### Boring Technology
- No custom protocols (use Kafka, Avro, Iceberg standards)
- No experimental features
- Proven at scale by other companies

### Graceful Degradation
- Query timeouts return partial results
- API rate limiting instead of crashes
- Consumer continues on single message failure
- Monitoring shows degradation state

---

## Decision Authority

| Decision Type | Authority | Process |
|---------------|-----------|---------|
| Core principles | Tech Lead + Team Vote | RFC required |
| Technology stack | Tech Lead | ADR required |
| Component design | Senior Engineer | ADR in phase DECISIONS.md |
| Implementation details | Engineer | Code review |

---

## Architectural Constraints

### Phase 1 Constraints (Portfolio Demo)
- **Single-node deployment**: DuckDB (not distributed)
- **Local execution**: Docker Desktop on laptop
- **Small dataset**: ~10MB CSV files
- **No authentication**: Localhost only

### Production Constraints (Future)
- **Distributed query**: Migrate to Presto/Trino
- **Cloud deployment**: AWS or GCP
- **Large dataset**: 100x-1000x current volume
- **Multi-region**: Active-active replication

---

## Scalability Considerations

### Horizontal Scaling Paths

**Kafka**: Add more brokers and partitions
**Iceberg**: Already distributed (S3/MinIO storage)
**Query Engine**: Replace DuckDB with Presto cluster
**API**: Deploy multiple FastAPI instances behind load balancer
**Consumer**: Deploy multiple consumers in consumer group

### Vertical Scaling Paths

**DuckDB**: Increase memory for larger queries
**Kafka**: Increase broker memory and disk
**MinIO**: Add more storage nodes

---

## Migration Paths

### Phase 1 → Phase 2 (Production Prep)
1. Replace DuckDB with Presto/Trino
2. Add authentication (OAuth2 + JWT)
3. Deploy to cloud (AWS/GCP)
4. Add RBAC and governance (Apache Ranger)
5. Implement distributed caching (Redis)

**Estimated Effort**: 4-6 weeks

### Phase 2 → Phase 3 (Scale)
1. Multi-region Kafka replication (MirrorMaker 2)
2. Active-active query regions
3. Advanced monitoring (distributed tracing)
4. Chaos engineering and load testing

**Estimated Effort**: 6-8 weeks

---

## Related Documentation

- **Detailed Design**: [../design/](../design/)
- **Implementation Plan**: [../phases/phase-1-portfolio-demo/IMPLEMENTATION_PLAN.md](../phases/phase-1-portfolio-demo/IMPLEMENTATION_PLAN.md)
- **Operations**: [../operations/](../operations/)
- **Testing**: [../testing/](../testing/)

---

**Maintained By**: Engineering Team
**Review Frequency**: Quarterly
**Last Review**: 2026-01-10
