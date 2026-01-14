# Phase 2 Prep: Schema Evolution + Binance Streaming

**Status**: ✅ **COMPLETE**
**Target**: Foundation for Phase 2 Demo Enhancements
**Last Updated**: 2026-01-13
**Achievement**: Completed in 5.5 days vs 13-18 day estimate (61% faster than planned)

---

## Overview

Phase 2 Prep establishes the architectural foundation required for Phase 2 Demo Enhancements. This phase combines schema evolution (Step 0) and live streaming capability (Phase 1.5) to ensure the platform can scale across multiple data sources with industry-standard schemas.

### Why This Phase?

**Problem**: Current schemas are ASX vendor-specific and won't generalize to other exchanges. Platform only supports batch CSV ingestion, not live streaming.

**Solution**:
1. **Schema Evolution (Step 0)**: Migrate to industry-standard hybrid schemas that work across asset classes
2. **Binance Streaming (Phase 1.5)**: Add live WebSocket streaming to demonstrate true streaming capability

### Goals

1. **Multi-Source Compatibility**: Industry-standard schemas work across ASX, Binance, and future exchanges
2. **Streaming Capability**: Live cryptocurrency data streaming via WebSocket
3. **Production Readiness**: Level 3 error handling with circuit breakers and failover
4. **Demo Enhancement**: Live streaming makes demo significantly more impressive

---

## Quick Links

- [Implementation Plan](./IMPLEMENTATION_PLAN.md) - Full 15-step implementation plan
- [Progress Tracker](./PROGRESS.md) - Detailed progress with % complete
- [Decision Log](./DECISIONS.md) - Architectural decision records
- [Validation Guide](./VALIDATION_GUIDE.md) - How to validate phase completion
- [Status Snapshot](./STATUS.md) - Current status and blockers

---

## Implementation Structure

### Part 1: Schema Evolution (Step 0)

7 substeps to migrate from v1 vendor-specific schemas to v2 industry-standard schemas.

| Step | Focus | Duration | Status |
|------|-------|----------|--------|
| [00.1](./steps/step-00.1-design-v2-schemas.md) | Design v2 Avro schemas | 4 hours | ✅ Complete |
| [00.2](./steps/step-00.2-update-producer.md) | Update producer for v2 | 6 hours | ✅ Complete |
| [00.3](./steps/step-00.3-update-consumer.md) | Update consumer for v2 | 6 hours | ✅ Complete |
| [00.4](./steps/step-00.4-update-batch-loader.md) | Update batch loader for v2 | 4 hours | ✅ Complete |
| [00.5](./steps/step-00.5-update-query-engine.md) | Update query engine for v2 | 5 hours | ✅ Complete |
| [00.6](./steps/step-00.6-update-tests.md) | Update tests for v2 | 4 hours | ✅ Complete |
| [00.7](./steps/step-00.7-documentation.md) | Document v2 schemas | 3 hours | ✅ Complete |

**Total**: 3-4 days (32 hours) - **Completed in ~4 days**

### Part 2: Binance Streaming (Phase 1.5)

8 substeps to add live cryptocurrency streaming via Binance WebSocket API.

| Step | Focus | Duration | Status |
|------|-------|----------|--------|
| [01.5.1](./steps/step-01.5.1-binance-websocket-client.md) | WebSocket client | 6 hours | ✅ Complete |
| [01.5.2](./steps/step-01.5.2-message-conversion.md) | Message conversion to v2 | 4 hours | ✅ Complete |
| [01.5.3](./steps/step-01.5.3-streaming-service.md) | Streaming daemon service | 6 hours | ✅ Complete |
| [01.5.4](./steps/step-01.5.4-error-handling.md) | Production resilience | 6 hours | ✅ Complete |
| [01.5.5](./steps/step-01.5.5-testing.md) | Unit + integration tests | 5 hours | ✅ Complete |
| [01.5.6](./steps/step-01.5.6-docker-compose.md) | Docker Compose integration | 3 hours | ✅ Complete |
| [01.5.7](./steps/step-01.5.7-demo-integration.md) | E2E validation | 6 hours | ✅ Complete |
| [01.5.8](./steps/step-01.5.8-documentation.md) | Documentation | 4 hours | ✅ Complete |

**Total**: 3-5 days (40 hours) - **Completed in ~1.5 days**

---

## Key Deliverables

### Schema Evolution (Step 0)

**New Files**:
- `src/k2/schemas/trade_v2.avsc` - Industry-standard trade schema
- `src/k2/schemas/quote_v2.avsc` - Industry-standard quote schema
- `docs/architecture/schema-design-v2.md` - v2 design documentation

**Modified Files**:
- `src/k2/ingestion/producer.py` - v2 schema support
- `src/k2/ingestion/consumer.py` - v2 deserialization
- `src/k2/ingestion/batch_loader.py` - CSV → v2 mapping
- `src/k2/query/engine.py` - Query v2 tables
- `src/k2/api/models.py` - v2 response models
- All unit tests updated for v2

### Binance Streaming (Phase 1.5)

**New Files**:
- `src/k2/ingestion/binance_client.py` - WebSocket client
- `scripts/binance_stream.py` - Streaming daemon
- `tests/unit/test_binance_client.py` - Unit tests
- `docs/architecture/streaming-architecture.md` - Streaming docs

**Modified Files**:
- `src/k2/common/config.py` - BinanceConfig added
- `docker-compose.yml` - binance-stream service
- `scripts/demo.py` - Live streaming showcase
- `README.md` - Live streaming section

---

## Dependencies

### Before Starting

- [x] Phase 1 complete (Steps 1-16)
- [x] All Phase 1 tests passing
- [x] Infrastructure running (Kafka, Iceberg, MinIO, PostgreSQL, Prometheus, Grafana)

### New Dependencies

**Python Packages**:
- `websockets` - Async WebSocket client for Binance

**Infrastructure**:
- No new infrastructure for Schema Evolution
- Binance WebSocket connection (external, no setup needed)

---

## Success Criteria

Phase 2 Prep is complete when:

### Schema Evolution (Step 0)
1. ✅ v2 schemas validate with avro-tools
2. ✅ Can load ASX CSV → v2 Kafka → v2 Iceberg → v2 API
3. ✅ vendor_data map contains ASX-specific fields
4. ✅ All fields present (message_id, side, currency, asset_class, etc.)
5. ✅ All tests pass (unit + integration)
6. ✅ Documentation complete (schema-design-v2.md)

### Binance Streaming (Phase 1.5)
1. ✅ Live BTC/ETH trades streaming from Binance → Kafka → Iceberg
2. ✅ Trades queryable via API within 2 minutes of ingestion
3. ✅ Demo showcases live streaming (terminal + Grafana + API)
4. ✅ Production-grade resilience (exponential backoff, circuit breaker, alerting, failover)
5. ✅ Metrics exposed (connection status, messages received, errors)
6. ✅ Documentation complete (streaming-architecture.md)

### Overall
1. ✅ Platform supports both batch (CSV) and streaming (WebSocket) data sources
2. ✅ v2 schema works across equities (ASX) and crypto (Binance)
3. ✅ Ready for Phase 2 Demo Enhancements (circuit breakers, Redis, hybrid queries)

---

## Getting Started

### Prerequisites Check

```bash
# Verify Phase 1 infrastructure is running
docker compose ps

# Verify all services healthy
docker compose ps | grep -i "up"

# Run Phase 1 tests to ensure baseline
pytest tests/unit/ -v
pytest tests/integration/ -v
```

### Start Implementation

1. **Read the plan**: Review [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)
2. **Start with Step 00.1**: [Design v2 Schemas](./steps/step-00.1-design-v2-schemas.md)
3. **Track progress**: Update [PROGRESS.md](./PROGRESS.md) after each substep
4. **Log decisions**: Document in [DECISIONS.md](./DECISIONS.md)
5. **Commit per substep**: Use format `feat(schema): <description>`

---

## Timeline

**Estimated Duration**: 13-18 days total

- **Week 1-2**: Schema Evolution (Step 0) - 3-4 days
- **Week 2-3**: Binance Streaming (Phase 1.5) - 3-5 days
- **Buffer**: 1-2 days for unexpected issues

**Critical Path**: Schema Evolution must complete before Binance Streaming (Binance uses v2 schemas)

---

## Reference Documentation

- [Schema Evolution Guide](./reference/schema-evolution-guide.md) - Migration patterns and best practices
- [Binance API Reference](./reference/binance-api-reference.md) - WebSocket API documentation
- [v2 Schema Specification](./reference/v2-schema-spec.md) - Field-by-field v2 schema docs
- [Testing Guide](./reference/testing-guide.md) - Test patterns for Phase 2 Prep

---

**Last Updated**: 2026-01-12
**Maintained By**: Implementation Team
