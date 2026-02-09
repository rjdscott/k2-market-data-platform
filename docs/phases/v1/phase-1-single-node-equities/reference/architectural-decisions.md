# K2 Platform - Architectural Decisions Summary

This document provides a high-level overview of architectural decisions and intentionally deferred features.

**For detailed ADRs**, see [../DECISIONS.md](../DECISIONS.md)

**Last Updated**: 2026-01-10

---

## Core Architectural Principles

### 1. Pragmatic Over Perfect
**Philosophy**: Deliver working software over theoretical perfection.
- Choose simplicity when complexity doesn't add proportional value
- Prefer proven patterns over bleeding-edge technology
- Document trade-offs explicitly (see DECISIONS.md)

### 2. Portfolio-First Design
**Context**: This is Phase 1 - a portfolio demonstration, not production deployment.
- Optimize for local execution (laptop-friendly)
- Prioritize clarity and demonstrability
- Document productionization path in README

### 3. Test-Alongside Development
**Approach**: Not strict TDD, but tests written concurrently with implementation.
- Unit tests for business logic
- Integration tests for component interactions
- E2E test for complete workflow validation
- Target: 80%+ coverage

---

## Key Architectural Decisions

See [DECISIONS.md](../DECISIONS.md) for complete records. Summary:

| Decision | Rationale | Trade-Off |
|----------|-----------|-----------|
| **DuckDB over Spark** | Embedded simplicity, sub-second queries | Single-node limitation |
| **Daily partitioning** | Optimizes common time-range queries | Intraday queries scan full day |
| **At-least-once delivery** | Simplicity + no data loss | Potential duplicates (acceptable) |
| **Embedded architecture** | Easy local execution | Not production-scale |

---

## Technology Stack Rationale

### Streaming: Apache Kafka
**Why**: Industry standard, proven at scale, rich ecosystem
- Confluent Python client (official support)
- Schema Registry for evolution
- Easy to scale (add brokers/partitions)

### Lakehouse: Apache Iceberg
**Why**: ACID guarantees, time-travel, schema evolution
- Query engine agnostic (DuckDB → Presto migration path)
- Partition evolution without rewriting data
- Open table format (no vendor lock-in)

### Query: DuckDB
**Why**: Embedded, fast analytics, native Iceberg support
- Phase 1: Perfect for demo (sub-second queries)
- Phase 2+: Clear migration to Presto/Trino

### API: FastAPI
**Why**: Modern Python framework, auto-documentation, async support
- Pydantic models for type safety
- OpenAPI/Swagger out-of-the-box
- Fast development iteration

### Observability: Prometheus + Grafana
**Why**: Open-source standard, pull-based metrics, rich visualizations
- Easy Docker deployment
- Wide integration ecosystem
- Industry-standard query language (PromQL)

---

## What's Intentionally NOT Included

**Important**: These are conscious decisions, not oversights. They're documented here to demonstrate architectural judgment.

### 1. Complex Governance
**Deferred to Phase 2+**

❌ **Not Implementing**:
- RBAC with role hierarchies
- Row-level security
- Field-level encryption
- Data lineage tracking
- Audit logging with tamper-proofing

✅ **Why Deferred**:
- Too complex for portfolio demonstration
- Adds 40+ hours of implementation
- Basic audit logging (structlog) is sufficient for Phase 1
- Production would use dedicated governance platform (Atlan, Collibra)

✅ **Future Path**:
- Integrate Apache Ranger for RBAC
- Use column masking in query layer
- Add audit table for all writes

---

### 2. GraphQL API
**Deferred to Phase 2+**

❌ **Not Implementing**:
- GraphQL schema and resolvers
- Nested query optimization
- Subscription support for real-time data

✅ **Why Deferred**:
- REST API demonstrates API design skills
- GraphQL adds complexity without proportional demo value
- Market data use cases fit REST pattern well

✅ **Future Path**:
- Add Strawberry or Ariadne for GraphQL layer
- Keep REST for simple queries
- Use GraphQL for complex nested data

---

### 3. Performance Load Testing
**Deferred to Phase 2+**

❌ **Not Implementing**:
- Load testing with JMeter/Locust
- Chaos engineering
- Failure injection tests
- Multi-region replication
- Performance benchmarking under load

✅ **Why Deferred**:
- Functional correctness is priority for portfolio
- Load testing requires production-scale infrastructure
- Current scope validates basic performance (< 5s queries)

✅ **Future Path**:
- Load test API at 1000 req/sec
- Test consumer throughput (messages/sec)
- Benchmark query latency under load
- Chaos testing (kill services, network partitions)

---

### 4. Advanced Query Optimization
**Deferred to Phase 2+**

❌ **Not Implementing**:
- Query result caching (Redis)
- Materialized views
- Pre-aggregation tables
- Columnar caching layer
- Query optimization hints

✅ **Why Deferred**:
- DuckDB is already fast (sub-second queries)
- Caching adds operational complexity
- Portfolio demonstrates query capability, not optimization

✅ **Future Path**:
- Add Redis for frequently accessed queries
- Create materialized OHLCV summary tables
- Implement cache invalidation strategy

---

### 5. Multi-Region Replication
**Deferred to Phase 2+**

❌ **Not Implementing**:
- Multi-region Kafka clusters
- Cross-region Iceberg replication
- Geographic load balancing
- Disaster recovery automation

✅ **Why Deferred**:
- Out of scope for Phase 1 portfolio demo
- Requires cloud infrastructure (AWS/GCP)
- Adds significant complexity

✅ **Future Path**:
- MirrorMaker 2 for Kafka replication
- Iceberg table replication via S3 cross-region
- Active-active query regions

---

### 6. Advanced Observability
**Deferred to Phase 2+**

❌ **Not Implementing**:
- Distributed tracing (Jaeger/Zipkin)
- Complex alerting rules (Alertmanager)
- SLO/SLI tracking
- Error budget management
- Log aggregation (ELK/Loki)

✅ **Why Deferred**:
- Basic Prometheus metrics sufficient for demo
- Distributed tracing requires instrumentation across all components
- Alerting requires on-call rotation (not applicable to portfolio)

✅ **Future Path**:
- Add OpenTelemetry for distributed tracing
- Configure Alertmanager with PagerDuty
- Define SLOs (99.9% uptime, p99 < 500ms)
- Aggregate logs to Loki or CloudWatch

---

### 7. Authentication & Authorization
**Intentionally Minimal in Phase 1**

❌ **Not Implementing**:
- User authentication (OAuth2, JWT)
- API key management
- Rate limiting per user
- Access control lists

✅ **Why Minimal**:
- Portfolio demo runs locally (no public internet)
- Auth implementation well-understood (not differentiated skill)
- Can be added quickly if needed

✅ **Future Path**:
- Add FastAPI OAuth2 with JWT tokens
- Integrate with Auth0 or Okta
- Implement rate limiting with Redis
- Add API key rotation

---

### 8. Exactly-Once Semantics
**Intentionally Using At-Least-Once**

❌ **Not Implementing**:
- Kafka transactions for exactly-once
- Distributed transaction coordinator
- Strict deduplication

✅ **Why At-Least-Once**:
- Market data tolerates occasional duplicates
- Iceberg handles idempotent writes
- Kafka transactions add 2-3x latency
- Simpler implementation and debugging

✅ **Future Path**:
- Upgrade to Kafka transactions if strict dedup required
- Add deduplication layer in consumer
- Use Kafka Streams for exactly-once processing

---

## Migration Paths to Production

### Scaling Query Layer
**Current**: DuckDB (embedded, single-node)
**Future**: Presto or Trino cluster

**Migration Steps**:
1. Deploy Presto/Trino cluster
2. Update query engine to use Presto JDBC
3. Keep DuckDB for development
4. Add horizontal scaling (multiple query workers)
5. Implement query result caching

**Effort**: 2-3 weeks

---

### Scaling Ingestion
**Current**: Single consumer instance
**Future**: Multiple consumers in consumer group

**Migration Steps**:
1. Deploy consumer as Kubernetes deployment
2. Scale replicas (Kafka auto-assigns partitions)
3. Add consumer lag monitoring
4. Implement backpressure handling

**Effort**: 1 week

---

### Adding Governance
**Current**: No RBAC or data masking
**Future**: Apache Ranger integration

**Migration Steps**:
1. Deploy Apache Ranger
2. Define policies (table-level, column-level)
3. Integrate with query layer
4. Add audit logging
5. Implement data lineage

**Effort**: 4-6 weeks

---

## References

- [Full ADR Log](../DECISIONS.md)
- [Testing Strategy](testing-summary.md)
- [Success Criteria](success-criteria.md)
- [Verification Checklist](verification-checklist.md)

---

**Maintained By**: Implementation Team
**Review Frequency**: End of each sprint
