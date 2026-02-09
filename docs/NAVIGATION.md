# K2 Documentation Navigation Guide

**Last Updated**: 2026-01-22
**Purpose**: Role-based documentation paths to help you find what you need in <2 minutes

---

## Quick Navigation by Role

### New Engineer (30-minute onboarding path)

**Goal**: Understand what K2 is, how it works, and get your first query running

**Path**:
1. **Start Here** (5 min): [README.md](../README.md)
   - Platform overview and positioning (L3 Cold Path)
   - Technology stack at a glance
   - Quick setup commands

2. **Understand the Architecture** (10 min): [docs/architecture/platform-principles.md](./architecture/platform-principles.md)
   - 6 core principles (replayability, schema-first, boring technology, etc.)
   - Why K2 makes certain trade-offs
   - What makes this different from HFT platforms

3. **See It Work** (5 min): [demo notebooks](../notebooks/)
   - Run the Binance demo notebook
   - See real-time crypto trades flowing through the platform
   - Query 69,666+ validated messages

4. **Run Your First Query** (5 min): Query the database
   ```bash
   # Start the platform
   docker compose up -d

   # Run a query
   docker compose exec api curl "http://localhost:8000/api/v1/trades?symbol=BTCUSDT&limit=10"
   ```

5. **Explore the Code** (5 min): Key files to understand
   - [src/k2/ingestion/binance_client.py](../src/k2/ingestion/binance_client.py) - How we ingest real-time data
   - [src/k2/ingestion/consumer.py](../src/k2/ingestion/consumer.py) - How we write to Iceberg
   - [src/k2/query/engine.py](../src/k2/query/engine.py) - How we query with DuckDB

**Next Steps**:
- Read [Phase Guide](./phases/PHASE-GUIDE.md) to understand what's been built
- Explore [Testing Strategy](./testing/strategy.md) to understand quality standards

---

### Operator/On-Call Engineer (15-minute emergency path)

**Goal**: Quickly diagnose and fix production issues

**Critical Runbooks**:
1. [Binance Streaming Issues](./operations/runbooks/binance-streaming.md)
   - Connection drops → Page 209-260 (4 troubleshooting scenarios)
   - Messages not reaching Kafka → Line 262-320
   - High latency (>500ms) → Line 322-382
   - Parsing errors → Line 384-445

2. [Connection Pool Issues](./operations/runbooks/connection-pool-tuning.md)
   - Pool exhaustion → Line 85-140
   - Connection leaks → Line 142-198
   - High query latency → Line 200-256

3. [Failure Recovery](./operations/runbooks/failure-recovery.md)
   - Kafka consumer lag recovery
   - Iceberg write failures
   - Query timeout handling

**Monitoring Dashboards**:
- Grafana: http://localhost:3000 (default credentials in README)
- Prometheus: http://localhost:9090
- Key Metrics:
  - `binance_connection_state` - Connection health (1 = connected)
  - `kafka_messages_produced_total` - Messages flowing to Kafka
  - `iceberg_write_errors_total` - Write failures
  - `connection_pool_active` - Active query connections

**Alerting**:
- [Alerting Rules](./operations/monitoring/alerting-rules.yml) - All configured alerts
- Critical alerts: BinanceDisconnected, KafkaConsumerLag, IcebergWriteFailures

**Emergency Contacts**:
- Platform Team: #k2-platform-alerts (Slack)
- On-Call: PagerDuty rotation

---

### API Consumer (20-minute integration path)

**Goal**: Integrate with K2 API and query market data

**Path**:
1. **API Overview** (5 min): [API Documentation](http://localhost:8000/docs) (Swagger UI)
   - Available endpoints (trades, quotes, OHLCV analytics)
   - Request/response schemas
   - Authentication via X-API-Key header

2. **Query Trades** (5 min): GET /api/v1/trades
   ```bash
   # Authentication required
   export API_KEY="k2-dev-api-key-2026"

   # Get recent BTC trades
   curl -H "X-API-Key: $API_KEY" \
     "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=100"

   # Get trades in time range
   curl -H "X-API-Key: $API_KEY" \
     "http://localhost:8000/v1/trades?symbol=BTCUSDT&start_time=2026-01-13T00:00:00Z"
   ```

3. **Query OHLCV Analytics** ⭐ NEW (5 min): GET /v1/ohlcv/{timeframe}
   ```bash
   # Get 1-hour candles (last 24 hours)
   curl -H "X-API-Key: $API_KEY" \
     "http://localhost:8000/v1/ohlcv/1h?symbol=BTCUSDT&limit=24"

   # Batch query multiple timeframes
   curl -X POST -H "X-API-Key: $API_KEY" \
     -H "Content-Type: application/json" \
     "http://localhost:8000/v1/ohlcv/batch" \
     -d '[{"symbol": "BTCUSDT", "timeframe": "1h", "limit": 24}]'

   # Check OHLCV data health
   curl "http://localhost:8000/v1/ohlcv/health"
   ```
   See: [API Reference - OHLCV Endpoints](./reference/api-reference.md#ohlcv-endpoints)

4. **Understand the Data** (5 min): [Data Dictionary V2](./reference/data-dictionary-v2.md)
   - TradeV2 schema (line 15-88)
   - Field types and precision (Decimal 18,8)
   - Vendor-specific data (vendor_data map)
   - Example records (line 90-150)

5. **Security & Rate Limits** (5 min): [Security Features](./reference/security-features.md)
   - Rate Limits: 100 req/min for OHLCV, 20 req/min for batch (per API key)
   - Max response size: 10,000 rows per query
   - Timeout: 30 seconds
   - SQL injection protection, circuit breakers
   - Pagination: Use `offset` and `limit` parameters

**SDKs & Examples**:
- Python: [notebooks/binance-demo-principal.ipynb](../notebooks/binance-demo-principal.ipynb)
- JavaScript: Coming in Phase 3
- Example queries: [Data Dictionary V2](./reference/data-dictionary-v2.md#query-examples)

---

### Contributor/Developer (45-minute deep-dive path)

**Goal**: Understand the system deeply enough to contribute code

**Path**:

#### Part 1: Architecture (15 min)
1. [Platform Principles](./architecture/platform-principles.md) - The "constitution"
2. [System Design](./architecture/README.md) - Component diagram and layers
3. [Technology Stack](./architecture/technology-stack.md) - Why we chose each technology
   - When to replace DuckDB with Presto (>10TB or >100 users)
   - Trade-offs for each technology choice

#### Part 2: Design Details (15 min)
1. [Query Architecture](./design/query-architecture.md) - How queries flow through the system
2. [Data Guarantees](./design/data-guarantees/) - Consistency, ordering, quality
   - [Consistency Model](./design/data-guarantees/consistency-model.md) - At-least-once delivery
   - [Ordering Guarantees](./design/data-guarantees/ordering-guarantees.md) - Per-symbol FIFO
3. [Schema Design V2](./architecture/schema-design-v2.md) - Hybrid schema approach

#### Part 3: Implementation (10 min)
1. [Phase Guide](./phases/PHASE-GUIDE.md) - What's been built in each phase
2. [Phase 2 Completion Report](phases/v1/phase-2-prep/COMPLETION-REPORT.md) - Recent work (V2 schema + Binance)
3. [Streaming Sources Architecture](./architecture/streaming-sources.md) - How to add new data sources
   - Generic pattern (line 15-200)
   - Binance reference implementation (line 202-400)

#### Part 4: Testing & Operations (5 min)
1. [Testing Strategy](./testing/strategy.md) - Test pyramid and coverage requirements
2. [Validation Procedures](./testing/validation-procedures.md) - E2E validation checklist
3. [Connection Pool Tuning](./operations/runbooks/connection-pool-tuning.md) - Performance optimization

**Development Workflow**:
1. Read relevant design docs
2. Check phase DECISIONS.md for recent ADRs
3. Write tests first (TDD)
4. Implement feature
5. Update documentation
6. Run validation: `./scripts/validate-docs.sh`
7. Commit with descriptive message

**Key Code Paths**:
- **Ingestion**: src/k2/ingestion/ (producer, consumer, binance_client)
- **Storage**: src/k2/storage/ (Iceberg table management)
- **Query**: src/k2/query/ (DuckDB connection pool, query engine)
- **API**: src/k2/api/ (FastAPI endpoints, middleware)
- **Common**: src/k2/common/ (metrics, connection pool, utils)

**Development Tools**:
```bash
# Run tests-backup
uv run pytest

# Type checking
uv run mypy src/

# Linting
uv run ruff check src/

# Format code
uv run ruff format src/

# Validate documentation
./scripts/validate-docs.sh
```

---

## Documentation by Category

### Architecture (Permanent, High-Level)
What the system is and why it's designed this way

- [Platform Principles](./architecture/platform-principles.md) - Core design philosophy
- [System Design](./architecture/README.md) - Component overview
- [Platform Positioning](./architecture/platform-positioning.md) - L3 Cold Path vs HFT
- [Technology Stack](./architecture/technology-stack.md) - Technology decisions with trade-offs
- [Streaming Sources](./architecture/streaming-sources.md) - WebSocket integration pattern
- [Schema Design V2](./architecture/schema-design-v2.md) - Hybrid schema approach
- [Alternative Architectures](./architecture/alternatives.md) - What we rejected and why

### Design (Component-Level Details)
How components work and interact

- [Query Architecture](./design/query-architecture.md) - Query flow and optimization
- [Data Guarantees](./design/data-guarantees/) - Consistency, ordering, quality
  - [Consistency Model](./design/data-guarantees/consistency-model.md)
  - [Ordering Guarantees](./design/data-guarantees/ordering-guarantees.md)
  - [Correctness Trade-offs](./design/data-guarantees/correctness-tradeoffs.md)
  - [Data Quality](./design/data-guarantees/data-quality.md)

### Operations (Running the Platform)
How to operate, monitor, and troubleshoot

- **Runbooks**:
  - [Binance Streaming](./operations/runbooks/binance-streaming.md) - Binance troubleshooting guide
  - [Kraken Streaming](./operations/runbooks/kraken-streaming.md) - Kraken WebSocket guide
  - [Connection Pool Tuning](./operations/runbooks/connection-pool-tuning.md) - Performance optimization
  - [Failure Recovery](./operations/runbooks/failure-recovery.md) - Disaster recovery
- **Monitoring**:
  - Prometheus metrics exposed at :9090
  - Grafana dashboards at :3000
  - Alert rules in monitoring/
- **Cost Management**:
  - [Cost Model](./operations/cost-model.md) - FinOps at 3 scales

### Testing (Quality Assurance)
How we ensure correctness

- [Testing Strategy](./testing/strategy.md) - Test pyramid and philosophy
- [Validation Procedures](./testing/validation-procedures.md) - E2E validation checklist

### Phases (Implementation Progress)
What's been built and when

- [Phase Guide](./phases/PHASE-GUIDE.md) - Visual timeline and FAQ
- [Phase 0 - Technical Debt Resolution](phases/v1/phase-0-technical-debt-resolution/) COMPLETE
- [Phase 1 - Single-Node Implementation](phases/v1/phase-1-single-node-equities/) COMPLETE
- [Phase 2 - Multi-Source Foundation](phases/v1/phase-2-prep/) COMPLETE (V2 Schema + Binance)
- [Phase 3 - Demo Enhancements](phases/v1/phase-3-demo-enhancements/) COMPLETE (Circuit breaker, Hybrid queries, Cost model)
- [Phase 4 - Demo Readiness](phases/v1/phase-4-demo-readiness/) COMPLETE (9/10 steps, 135/100 score)

### Reference (Quick Lookup)
Field definitions, APIs, configuration

- [Data Dictionary V2](./reference/data-dictionary-v2.md) - Field-by-field schema reference
- [Versioning Policy](./reference/versioning-policy.md) - Semantic versioning rules
- [API Reference](http://localhost:8000/docs) - Swagger UI (when running)

### Reviews (Historical Assessments)
What experts said about the platform

- [Review Navigation Index](./reviews/README.md) - All reviews organized by category
- [Principal Demo Review](./reviews/2026-01-11-principal-data-engineer-demo-review.md) - Executive assessment
- [Staff Engineer Assessment](./reviews/2026-01-17-staff-engineer-comprehensive-assessment.md) - Comprehensive review
- [Architecture Review](./reviews/architecture-review.md) - Technical deep-dive
- [Project Review](./reviews/project-review.md) - Full project assessment

---

## Common Questions

### "Where do I start?"
→ [README.md](../README.md) for overview, then follow your role path above

### "How do I add a new data source?"
→ [Streaming Sources Architecture](./architecture/streaming-sources.md) - Complete integration guide

### "How do I troubleshoot Binance connection issues?"
→ [Binance Streaming Runbook](./operations/runbooks/binance-streaming.md) - 4 major troubleshooting scenarios

### "What are the data quality guarantees?"
→ [Data Guarantees](./design/data-guarantees/) - Consistency, ordering, quality models

### "When should I replace DuckDB with Presto?"
→ [Technology Stack](./architecture/technology-stack.md#4-query-engine-duckdb-010--presto-migration-planned) - >10TB or >100 concurrent users

### "What's the difference between TradeV1 and TradeV2?"
→ [Data Dictionary V2](./reference/data-dictionary-v2.md) - V2 uses hybrid schema with vendor_data map

### "How do I run the platform locally?"
→ [README.md](../README.md#quick-start) - Docker Compose setup

### "Where are the API docs?"
→ http://localhost:8000/docs (Swagger UI when platform is running)

### "How do I contribute?"
→ Follow the [Contributor path](#%F0%9F%91%A8%E2%80%8D%F0%9F%92%BB-contributordeveloper-45-minute-deep-dive-path) above

### "What testing is required for PRs?"
→ [Testing Strategy](./testing/strategy.md) - Test pyramid and coverage requirements (80% unit, 60% integration)

---

## Documentation Health Metrics

**Last Validation**: 2026-01-22 (v1.0 consolidation)
**Status**: PASSING

**Metrics**:
- Total Documents: ~180 (reduced from 317)
- Review Docs: 5 (reduced from 22)
- Archived Step Files: 68
- Empty Directories: 0
- Root Orphan Docs: 1 (TECHNICAL_DEBT.md - referenced)

**Validation Command**:
```bash
./scripts/validate-docs.sh
```

**Documentation Grade**: **A (9.5/10)** (post-consolidation)

---

## Tips for Effective Documentation Use

1. **Use Ctrl+F / Cmd+F liberally** - All docs are text-searchable
2. **Follow links** - Documentation is highly cross-referenced
3. **Check "Last Updated" dates** - Prioritize recent docs
4. **Start with architecture, then drill into design** - Top-down approach
5. **Use runbooks for operations** - Step-by-step troubleshooting
6. **Validate after changes**: Run `./scripts/validate-docs.sh`
7. **Ask questions in #k2-platform** (Slack) if stuck >15 minutes

---

## Meta: About This Guide

**Purpose**: Help anyone find relevant documentation in <2 minutes
**Audience**: All roles (engineers, operators, API consumers)
**Maintained By**: Platform Team
**Update Frequency**: After major documentation changes
**Feedback**: #k2-platform (Slack) or create GitHub issue

**Last Updated**: 2026-01-22
**Next Review**: 2026-02-22 (monthly)
