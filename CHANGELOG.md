# Changelog

All notable changes to the K2 Market Data Platform will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-02-09 - Preview Release

**Release Type**: Preview/Beta - Not Production Ready
**Status**: ⚠️ Contains known security vulnerabilities (see KNOWN-ISSUES.md)

This is the first tagged release of the K2 Market Data Platform, representing completion of Phases 0-13 with a functional L3 Cold Path research data platform. This preview release is suitable for development, testing, and evaluation but **should not be used in production** until security issues are resolved in v0.2.

### Major Features

#### Core Platform (Phases 0-7)
- **Single-node market data lakehouse** with Apache Kafka, Iceberg, and DuckDB
- **Multi-source ingestion**: CSV batch loading + WebSocket streaming (Binance, Kraken)
- **V2 schema architecture**: Hybrid approach with core fields + vendor_data map
- **REST API**: FastAPI-based query interface with authentication
- **Observability**: 50+ Prometheus metrics, Grafana dashboards, distributed tracing
- **CI/CD infrastructure**: GitHub Actions workflows, multi-tier test pyramid
- **Production resilience**: Circuit breakers, exponential backoff, blue-green deployment

#### Streaming Architecture (Phases 8-12)
- **Medallion architecture**: Bronze (raw) → Silver (validated) → Gold (unified) layers
- **Real-time ingestion**: Binance and Kraken WebSocket producers
- **Spark streaming jobs**: Structured streaming with Iceberg table writes
- **Cross-exchange aggregation**: Unified `gold_crypto_trades` table
- **Data quality**: Automated validation, sequence tracking, DLQ handling

#### OHLCV Analytics (Phase 13 - Beta)
- **5 timeframe tables**: 1m, 5m, 30m, 1h, 1d candles pre-computed
- **Prefect orchestration**: Automated scheduling with staggered execution
- **Incremental + batch processing**: MERGE for 1m/5m, INSERT OVERWRITE for 30m/1h/1d
- **Data quality validation**: 4 invariant checks (price relationships, VWAP bounds, etc.)
- **Retention policies**: 90 days to 5 years by timeframe
- **REST API endpoints**: `/v1/ohlcv/{timeframe}`, `/v1/ohlcv/batch`, `/v1/ohlcv/health`

### Added

#### Infrastructure
- Apache Kafka 3.7 (KRaft mode, no ZooKeeper)
- Apache Iceberg 1.4 lakehouse tables with ACID guarantees
- DuckDB 1.4 query engine with connection pooling
- Apache Spark 3.5 for distributed processing
- MinIO S3-compatible object storage
- PostgreSQL metadata store for Iceberg catalog
- Prefect 3.1 workflow orchestration
- Prometheus + Grafana monitoring stack

#### Data Ingestion
- CSV batch loader for historical archives
- Binance WebSocket streaming client (trades, quotes)
- Kraken WebSocket streaming client (trades, quotes)
- Schema Registry with Avro serialization (BACKWARD compatibility)
- Dead Letter Queue (DLQ) for failed messages
- Sequence tracking and gap detection
- Exponential backoff reconnection (1s → 128s)

#### Storage Layer
- Iceberg tables with daily partitioning
- Time-travel query support (snapshot isolation)
- ZSTD compression (64MB target file size)
- Three-layer medallion architecture:
  - Bronze: Raw ingestion (1:1 with source)
  - Silver: Validated + schema-normalized
  - Gold: Cross-exchange unified view
- OHLCV analytical tables (5 timeframes)

#### Query Engine
- DuckDB connection pool with configurable sizing
- REST API with 7 endpoints (trades, quotes, OHLCV)
- Sub-second query latency (<100ms point queries, 200-500ms aggregations)
- SQL injection protection via parameterized queries
- Pagination support (offset/limit)
- API key authentication (X-API-Key header)

#### Observability
- 50+ Prometheus metrics (ingestion, storage, query, system)
- 5 Grafana dashboards (overview, ingestion, storage, query, OHLCV)
- Health check endpoints (`/health`, `/v1/ohlcv/health`)
- Distributed tracing (OpenTelemetry ready)
- Kafka UI for topic monitoring
- Spark UI for job monitoring
- Prefect UI for workflow monitoring

#### Testing
- 260+ tests across 6 tiers (unit, integration, performance, chaos, operational, soak)
- 28 end-to-end tests validating full pipeline
- Multi-environment test support (unit, integration, heavy, chaos)
- GitHub Actions CI/CD with PR validation (<5 min feedback)
- Automated dependency updates via Dependabot
- Resource management fixtures (GC, leak detection)

#### Documentation
- 180+ markdown documents (architecture, design, operations, testing, phases)
- Role-based navigation guide (engineer, operator, API consumer, contributor)
- 9 operational runbooks (Binance, Kraken, connection pool, circuit breaker, etc.)
- 30+ architectural decision records (ADRs)
- Comprehensive API documentation (Swagger/OpenAPI)
- Phase-specific implementation tracking (0-13)

### Changed

#### Schema Evolution (Phase 2)
- **V1 → V2 migration**: Added hybrid schema approach
- Core standard fields + `vendor_data` map for exchange-specific data
- Decimal(18,8) precision for price/quantity fields
- Microsecond timestamps (`timestamp_micros` as Long)
- Nullable `source_sequence` for exchanges without sequence numbers

#### Query Architecture (Phase 3)
- Added circuit breaker with 5-level degradation
- Hybrid query engine (Kafka tail + Iceberg merge for recent data)
- Connection pool tuning (10 min → 5 min connection lifetime)

#### API Changes (Phase 13)
- Added 3 OHLCV endpoints: `GET /v1/ohlcv/{timeframe}`, `POST /v1/ohlcv/batch`, `GET /v1/ohlcv/health`
- Enhanced error responses with detailed messages
- Added timeframe validation (1m, 5m, 30m, 1h, 1d only)

### Fixed

#### Phase 0: Technical Debt Resolution (18.5 hours)
- TD-000: Prometheus metrics label mismatch in producer
- TD-001: Consumer sequence tracker API mismatch
- TD-002: DLQ JSON serialization of datetime/Decimal objects
- TD-003: Consumer E2E validation incomplete
- TD-004: Metrics unit tests missing (36 tests added)
- TD-005: Pre-commit hook for metrics validation
- TD-006: Missing `reference_data_v2.avsc` schema

#### Production Stability (Phases 5-7)
- Memory leaks in WebSocket clients (bounded serializer cache)
- Connection rotation to prevent stale connections
- SSL certificate verification (was disabled in demo mode)
- Resource exhaustion in test suite (35 heavy tests excluded by default)

#### Data Quality (Phases 10-13)
- OHLC ordering bug in aggregation (explicit timestamp sort)
- VWAP calculation bounds checking
- Late-arriving trade handling via MERGE logic
- Sequence gap detection (0 gaps in 636K trades)

### Security

⚠️ **Known Vulnerabilities** (see KNOWN-ISSUES.md for details):
- SQL injection vulnerability in OHLCV LIMIT clause (HIGH severity)
- Resource exhaustion in batch endpoint (HIGH severity)
- Missing rate limiting on OHLCV endpoints (MEDIUM severity)
- Incomplete input validation (MEDIUM severity)
- No circuit breaker integration for OHLCV (LOW severity)

These issues are documented and tracked for resolution in v0.2.

### Deprecated

Nothing deprecated in this initial release.

### Removed

Nothing removed in this initial release.

### Performance

#### Achieved Metrics (Single-node, Docker Desktop)
- **Ingestion**: 138 msg/sec sustained (Kafka → Iceberg)
- **Point queries**: <100ms p95 (10K row scans)
- **Aggregations**: 200-500ms p95 (time-series rollups)
- **OHLCV jobs**: 6-15s for 1m, 10-20s for 5m (observed)
- **Uptime**: 99.99% (Binance streaming, 24h soak test)
- **Memory**: Stable at 200-250MB, <50MB growth over 24h

#### Known Limitations
- Single-node only (distributed scaling planned for future)
- DuckDB query engine (Presto/Trino migration planned for >10TB or >100 users)
- 2 exchanges (Binance, Kraken) - extensible to more
- 4 symbols (BTC, ETH) - configurable

---

## Release Statistics

### Development Effort
- **Total Phases**: 14 (Phases 0-13)
- **Duration**: ~6 months (Phase 0: Jan 13, Phase 13: Jan 21)
- **Implementation Steps**: 100+ across all phases
- **Test Count**: 260+ tests (170 unit, 60 integration, 30+ E2E/performance/chaos)
- **Code Coverage**: 80%+ unit, 60%+ integration

### Platform Maturity
- **Score Evolution**: 78 → 86 (Phase 0) → 92 (Phase 3) → 135/100 (Phase 4 demo readiness)
- **Documentation**: 180+ docs, A grade (9.5/10)
- **Production Readiness**: 85% (blocked by security issues)

### Technology Stack
- Python 3.13+
- Apache Kafka 3.7 (KRaft)
- Apache Iceberg 1.4
- Apache Spark 3.5
- DuckDB 1.4
- Prefect 3.1
- FastAPI 0.115
- PostgreSQL 16
- MinIO (S3-compatible)
- Prometheus + Grafana

---

## Upgrade Notes

### From Development to v0.1.0

No breaking changes. This is the first tagged release.

**Migration Steps**:
1. Pull latest code: `git pull origin main`
2. Rebuild services: `docker compose down && docker compose up -d --build`
3. Run migrations: `uv run python scripts/init_infra.py`
4. Verify health: `curl http://localhost:8000/health`

### Post-v0.1.0 Plans

**v0.2.0 (Security & Production Hardening)**:
- Fix SQL injection vulnerability in LIMIT clause
- Add rate limiting (100 req/min per API key)
- Implement batch endpoint timeout (30s max)
- Add circuit breaker to OHLCV endpoints
- Comprehensive input validation
- Security testing suite

**v0.3.0+ (Feature Enhancements)**:
- Additional exchanges (Coinbase, Kraken)
- More asset classes (equities, options, futures)
- Distributed query engine (Presto/Trino)
- Multi-region replication
- Kubernetes deployment
- Advanced analytics (order book reconstruction, market impact)

---

## Links

- **Repository**: https://github.com/rjdscott/k2-market-data-platform
- **Documentation**: [docs/NAVIGATION.md](./docs/NAVIGATION.md)
- **Known Issues**: [KNOWN-ISSUES.md](docs/archive/v1-platform/KNOWN-ISSUES.md)
- **Security**: [docs/reference/security-features.md](./docs/reference/security-features.md)
- **API Docs**: http://localhost:8000/docs (when running)

---

**Maintained By**: Engineering Team
**Next Release**: v0.2.0 (Security Hardening) - Target: 2026-02-20
