# Phase 2 Prep: Detailed Implementation Plan

**Phase**: Phase 2 Prep (Schema Evolution + Binance Streaming)
**Status**: Not Started (0/15 steps complete)
**Estimated Duration**: 13-18 days
**Last Updated**: 2026-01-12

---

## Overview

Phase 2 Prep establishes the foundation for Phase 2 Demo Enhancements by:
1. **Schema Evolution (Step 0)**: Migrating to industry-standard v2 schemas
2. **Binance Streaming (Phase 1.5)**: Adding live cryptocurrency streaming

**Critical Path**: Schema Evolution MUST complete before Binance Streaming (Binance uses v2 schemas).

---

## Part 1: Schema Evolution (Step 0)

**Duration**: 3-4 days (32 hours)
**Goal**: Migrate from v1 ASX vendor-specific schemas to v2 industry-standard hybrid schemas

### Problem Statement

Current v1 schemas don't scale to multiple data sources:
- ASX-specific fields: `company_id`, `qualifiers`, `venue`
- Ambiguous fields: `volume` (shares? BTC? contracts?)
- Missing fields: `message_id`, `side`, `trade_id`, `currency`, `asset_class`

### Solution: v2 Hybrid Schema

**Core Standard Fields** (all exchanges):
```
message_id, trade_id, symbol, exchange, asset_class, timestamp (micros),
price (Decimal 18,8), quantity (Decimal 18,8), currency, side (enum),
trade_conditions (array), source_sequence, ingestion_timestamp, platform_sequence
```

**Vendor Extensions**:
```
vendor_data: Map<string, string> (optional)
- ASX: {"company_id": "123", "qualifiers": "0", "venue": "X"}
- Binance: {"is_buyer_maker": "true", "event_type": "trade"}
```

### Implementation Steps

#### Step 00.1: Design v2 Schemas (4 hours)

**Deliverables**:
- `src/k2/schemas/trade_v2.avsc` - v2 trade schema
- `src/k2/schemas/quote_v2.avsc` - v2 quote schema
- Updated `src/k2/schemas/__init__.py` - v2 loading helpers

**Key Tasks**:
- Add all core standard fields
- Add vendor_data map (optional)
- Use timestamp-micros (not millis)
- Use Decimal (18,8) for price/quantity
- Add TradeSide enum (BUY, SELL, SELL_SHORT, UNKNOWN)

**Validation**:
- Schemas validate with `avro-tools`
- Python avro library can load schemas
- Field types correct (decimal, enum)

**Commit**: `feat(schema): add v2 industry-standard schemas with vendor extensions`

---

#### Step 00.2: Update Producer for v2 (6 hours)

**Deliverables**:
- Updated `src/k2/ingestion/producer.py`
- v2 message builder: `build_trade_v2()`

**Key Tasks**:
- Add schema_version parameter (default: "v2")
- Load v2 schemas
- Update produce_trade() for v2 fields
- Add _convert_to_v2() helper
- Update topic registration

**Validation**:
- Can produce v2 messages to Kafka
- v2 messages deserialize correctly
- Schema Registry shows v2 schemas

**Commit**: `feat(producer): add v2 schema support with backward compatibility`

---

#### Step 00.3: Update Consumer for v2 (6 hours)

**Deliverables**:
- Updated `src/k2/ingestion/consumer.py`
- Updated `src/k2/storage/writer.py`
- New Iceberg table: `market_data.trades_v2`

**Key Tasks**:
- Add schema_version config
- Update Avro deserializer for v2
- Update field mappings (quantity vs volume)
- Map vendor_data to JSON string in Iceberg
- Create new v2 tables

**Validation**:
- Consumer deserializes v2 messages
- v2 data writes to Iceberg
- vendor_data stored as JSON string
- Can query both v1 and v2 tables

**Commit**: `feat(consumer): add v2 schema support and iceberg table`

---

#### Step 00.4: Update Batch Loader for v2 (4 hours)

**Deliverables**:
- Updated `src/k2/ingestion/batch_loader.py`

**Key Tasks**:
- Add schema_version parameter
- Map CSV columns to v2 fields:
  - volume → quantity
  - side → side enum (buy → BUY, sell → SELL)
  - Add currency (default: "AUD" for ASX)
  - Add asset_class (default: "equities")
  - Generate message_id (UUID)
  - Map ASX-specific to vendor_data

**Validation**:
- Load sample ASX CSV → produces v2 messages
- vendor_data contains ASX-specific fields
- All trades have message_id, currency, asset_class

**Commit**: `feat(batch-loader): map csv to v2 schema with vendor extensions`

---

#### Step 00.5: Update Query Engine for v2 (5 hours)

**Deliverables**:
- Updated `src/k2/query/engine.py`
- Updated `src/k2/api/models.py` - v2 response models
- Updated `src/k2/api/v1/endpoints.py`

**Key Tasks**:
- Add table_version parameter (default: "v2")
- Query from market_data.trades_v2
- Update field references (volume → quantity)
- Parse vendor_data JSON if needed
- Update API response models

**Validation**:
- Query v2 trades by symbol
- Query v2 trades by side (BUY, SELL)
- Query v2 trades by currency
- API returns v2 format

**Commit**: `feat(query): add v2 table support with new fields`

---

#### Step 00.6: Update Tests (4 hours)

**Deliverables**:
- Updated unit tests
- Updated integration tests
- Updated test fixtures

**Key Tasks**:
- Update test_producer.py for v2
- Update test_consumer.py for v2
- Update test_query_engine.py for v2
- Update integration tests for v2 E2E
- Update test fixtures
- Verify coverage > 80%

**Validation**:
- All unit tests pass
- All integration tests pass
- Coverage > 80%

**Commit**: `test: update tests for v2 schema support`

---

#### Step 00.7: Documentation (3 hours)

**Deliverables**:
- Updated README.md
- `docs/architecture/schema-design-v2.md`
- Updated DECISIONS.md

**Key Tasks**:
- Add "Schema Evolution" section to README
- Create schema-design-v2.md
- Document field-by-field mappings
- Add Decision #015: Schema Evolution to v2
- Update demo script

**Commit**: `docs: document v2 schema design and migration`

---

## Part 2: Binance Streaming (Phase 1.5)

**Duration**: 3-5 days (40 hours)
**Goal**: Add live cryptocurrency streaming via Binance WebSocket API

### Overview

Add live cryptocurrency data streaming to demonstrate:
- True streaming capability (not just batch CSV)
- Multi-source compatibility (ASX + Binance)
- Production-grade resilience (Level 3 error handling)

**Scope**: BTC-USDT and ETH-USDT trade streams only

### Binance WebSocket API

**Endpoint**: `wss://stream.binance.com:9443/stream`
**Streams**: `btcusdt@trade`, `ethusdt@trade`

**Message Format**:
```json
{
  "e": "trade",
  "E": 1672531200000,  // Event time (ms)
  "s": "BTCUSDT",      // Symbol
  "t": 12345,          // Trade ID
  "p": "16500.00",     // Price
  "q": "0.05",         // Quantity
  "T": 1672531199900,  // Trade time
  "m": true,           // Is buyer maker
  "M": true            // Ignore
}
```

### Implementation Steps

#### Step 01.5.1: Binance WebSocket Client (6 hours)

**Deliverables**:
- `src/k2/ingestion/binance_client.py`
- Updated `src/k2/common/config.py` with BinanceConfig

**Key Tasks**:
- Create BinanceWebSocketClient class
- Connect to wss://stream.binance.com:9443/stream
- Subscribe to BTC-USDT, ETH-USDT trade streams
- Parse JSON messages
- Handle connection events (open, close, error)
- Add BinanceConfig to config.py

**Validation**:
- Connect to Binance WebSocket
- Receive and parse trade messages
- Handle disconnect/reconnect
- Log connection events

**Commit**: `feat(binance): add websocket client for trade streams`

---

#### Step 01.5.2: Message Conversion (4 hours)

**Deliverables**:
- Message converter in binance_client.py

**Key Tasks**:
- Add convert_binance_trade_to_v2() converter
- Map Binance fields to v2 schema
- Handle side mapping (buyer maker → SELL aggressor)
- Add vendor_data for Binance-specific fields
- Add validation and error handling

**Validation**:
- Binance message → v2 Trade (correct fields)
- vendor_data contains Binance-specific fields
- side mapping correct

**Commit**: `feat(binance): add message converter to v2 schema`

---

#### Step 01.5.3: Streaming Service (6 hours)

**Deliverables**:
- `scripts/binance_stream.py` daemon

**Key Tasks**:
- Create streaming daemon script
- Integrate BinanceWebSocketClient with MarketDataProducer
- Add CLI arguments (--symbols, --daemon, --log-level)
- Add graceful shutdown (SIGINT/SIGTERM)
- Test streaming: Binance → Kafka

**Validation**:
- Run python scripts/binance_stream.py
- See live trades in console logs
- Verify trades in Kafka
- Graceful shutdown with Ctrl+C

**Commit**: `feat(binance): add streaming service daemon`

---

#### Step 01.5.4: Error Handling & Resilience (6 hours)

**Deliverables**:
- Production-grade error handling in binance_client.py
- Prometheus metrics

**Key Tasks**:
- Add exponential backoff reconnection (5s → 60s max)
- Integrate circuit breaker (reuse from Phase 2)
- Add health checks (heartbeat every 30s)
- Add Prometheus metrics:
  - k2_binance_connection_status
  - k2_binance_messages_received_total
  - k2_binance_reconnects_total
  - k2_binance_errors_total
- Add alerting configuration
- Add failover endpoints

**Validation**:
- Simulate network failure → auto-reconnect
- Circuit breaker trips under high lag
- Metrics exposed
- Alerts trigger on disconnect

**Commit**: `feat(binance): add production resilience (circuit breaker, alerting, failover)`

---

#### Step 01.5.5: Testing (5 hours)

**Deliverables**:
- `tests/unit/test_binance_client.py`
- `tests/unit/test_binance_converter.py`
- 10+ unit tests

**Key Tasks**:
- Test message parsing
- Test v2 conversion
- Test side mapping
- Mock WebSocket connection
- Manual integration test (5 minutes live)
- Verify trades in Iceberg

**Validation**:
- All unit tests pass
- Manual test shows trades flowing end-to-end
- Can query Binance trades in Iceberg

**Commit**: `test: add binance client unit tests`

---

#### Step 01.5.6: Docker Compose Integration (3 hours)

**Deliverables**:
- Updated `docker-compose.yml`
- Optional: `scripts/start-streaming.sh`

**Key Tasks**:
- Add binance-stream service to docker-compose.yml
- Configure environment variables
- Add health check endpoint
- Test with docker compose up -d
- Verify logs show connection and trades

**Validation**:
- docker compose up -d starts Binance streamer
- Logs show connection and trades
- docker compose ps shows healthy

**Commit**: `feat(docker): add binance streaming service to compose`

---

#### Step 01.5.7: Demo Integration (6 hours)

**Deliverables**:
- Updated `scripts/demo.py`
- Grafana panel configuration
- Updated demo narrative

**Key Tasks**:
- Add live streaming display to demo.py
- Create Grafana panel: "Live BTC Price"
- Add API query demo for BTC trades
- Update demo narrative (Section 2: Ingestion)
- Test all three demo modes (terminal, Grafana, API)

**Validation**:
- Run python scripts/demo.py --section ingestion
- See live Binance trades in terminal
- Check Grafana panel shows live BTC price
- API query returns recent BTC trades

**Commit**: `feat(demo): integrate live binance streaming showcase`

---

#### Step 01.5.8: Documentation (4 hours)

**Deliverables**:
- Updated README.md
- `docs/architecture/streaming-architecture.md`
- Updated DECISIONS.md

**Key Tasks**:
- Add "Live Streaming" section to README
- Create streaming-architecture.md
- Add Decision #016: Binance Streaming Integration
- Update demo talking points
- Document Binance integration end-to-end

**Commit**: `docs: document binance streaming integration`

---

## Summary & Success Criteria

### Time Investment

| Phase | Estimated | Buffer | Total |
|-------|-----------|--------|-------|
| Schema Evolution (Step 0) | 32 hours | 4 hours | 3-4 days |
| Binance Streaming (Phase 1.5) | 40 hours | 8 hours | 3-5 days |
| **Total** | **72 hours** | **12 hours** | **13-18 days** |

### Success Criteria

**Schema Evolution (Step 0)**:
- ✅ v2 schemas validate with avro-tools
- ✅ Can load ASX CSV → v2 Kafka → v2 Iceberg → v2 API
- ✅ vendor_data map contains ASX-specific fields
- ✅ All fields present (message_id, side, currency, asset_class, etc.)
- ✅ All tests pass (unit + integration)
- ✅ Documentation complete (schema-design-v2.md)

**Binance Streaming (Phase 1.5)**:
- ✅ Live BTC/ETH trades streaming from Binance → Kafka → Iceberg
- ✅ Trades queryable via API within 2 minutes of ingestion
- ✅ Demo showcases live streaming (terminal + Grafana + API)
- ✅ Production-grade resilience (exponential backoff, circuit breaker, alerting, failover)
- ✅ Metrics exposed (connection status, messages received, errors)
- ✅ Documentation complete (streaming-architecture.md)

**Overall**:
- ✅ Platform supports both batch (CSV) and streaming (WebSocket) data sources
- ✅ v2 schema works across equities (ASX) and crypto (Binance)
- ✅ Ready for Phase 2 Demo Enhancements (circuit breakers, Redis, hybrid queries)

---

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| v2 schema breaks existing functionality | Medium | High | Comprehensive testing, validation guide |
| Binance WebSocket connection instability | Medium | Medium | Circuit breakers, auto-reconnect, failover |
| Performance degradation from schema changes | Low | Medium | Benchmark before/after, optimize as needed |

---

## Dependencies

**Before Starting**:
- ✅ Phase 1 complete (Steps 1-16)
- ✅ All Phase 1 tests passing
- ✅ Infrastructure running (Kafka, Iceberg, MinIO, PostgreSQL, Prometheus, Grafana)

**New Dependencies**:
- Python Packages:
  - `websockets>=12.0` - Async WebSocket client for Binance
- Infrastructure:
  - No new infrastructure for Schema Evolution
  - Binance WebSocket connection (external, no setup needed)

---

## Reference Links

- [Full Implementation Plan](/Users/rjdscott/.claude/plans/joyful-crafting-orbit.md)
- [Progress Tracking](PROGRESS.md)
- [Decision Log](DECISIONS.md)
- [Validation Guide](VALIDATION_GUIDE.md)
- [Status Snapshot](STATUS.md)

---

**Last Updated**: 2026-01-12
**Maintained By**: Implementation Team
