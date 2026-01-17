# Phase 2 Prep: Validation Guide

**Phase**: Phase 2 Prep (Schema Evolution + Binance Streaming)
**Purpose**: Comprehensive validation procedures and acceptance criteria
**Last Updated**: 2026-01-12

---

## Overview

This guide provides step-by-step validation procedures to ensure Phase 2 Prep implementation meets all requirements. Each substep has specific validation criteria that must pass before proceeding.

---

## Part 1: Schema Evolution (Step 0) Validation

### Step 00.1: Design v2 Schemas - Validation

#### Validation Checklist

- [ ] **Schema Files Exist**
  ```bash
  ls -la src/k2/schemas/trade_v2.avsc
  ls -la src/k2/schemas/quote_v2.avsc
  ```

- [ ] **Schemas Validate with avro-tools**
  ```bash
  # Install avro-tools if needed
  pip install avro-python3

  # Validate trade schema
  avro validate schema src/k2/schemas/trade_v2.avsc

  # Validate quote schema
  avro validate schema src/k2/schemas/quote_v2.avsc
  ```

- [ ] **Python Can Load Schemas**
  ```python
  from k2.schemas import load_avro_schema_v2

  trade_schema = load_avro_schema_v2('trade_v2')
  quote_schema = load_avro_schema_v2('quote_v2')

  print(f"Trade schema loaded: {trade_schema}")
  print(f"Quote schema loaded: {quote_schema}")
  ```

- [ ] **Required Fields Present**
  - Check trade_v2.avsc contains:
    - message_id (string, UUID)
    - trade_id (string)
    - symbol (string)
    - exchange (string)
    - asset_class (enum: equities, crypto, futures)
    - timestamp (long, logicalType: timestamp-micros)
    - price (bytes, logicalType: decimal, precision: 18, scale: 8)
    - quantity (bytes, logicalType: decimal, precision: 18, scale: 8)
    - currency (string)
    - side (enum: BUY, SELL, SELL_SHORT, UNKNOWN)
    - trade_conditions (array of strings)
    - source_sequence (long, optional)
    - ingestion_timestamp (long, logicalType: timestamp-micros)
    - platform_sequence (long, optional)
    - vendor_data (map, optional)

#### Acceptance Criteria

- ✅ All schema files exist
- ✅ Schemas validate without errors
- ✅ Python can load and parse schemas
- ✅ All required fields present with correct types
- ✅ Enums defined correctly (TradeSide, AssetClass)
- ✅ Decimal precision set to (18,8)
- ✅ Timestamp uses logicalType: timestamp-micros

---

### Step 00.2: Update Producer for v2 - Validation

#### Validation Checklist

- [ ] **Producer Can Build v2 Messages**
  ```python
  from k2.ingestion.producer import build_trade_v2
  from decimal import Decimal
  from datetime import datetime

  msg = build_trade_v2(
      symbol="BHP",
      exchange="ASX",
      asset_class="equities",
      timestamp=datetime.utcnow(),
      price=Decimal("45.67"),
      quantity=Decimal("1000"),
      currency="AUD",
      side="BUY",
      vendor_data={"company_id": "123", "qualifiers": "0"}
  )

  assert msg["message_id"] is not None
  assert msg["symbol"] == "BHP"
  assert msg["asset_class"] == "equities"
  assert msg["currency"] == "AUD"
  assert "vendor_data" in msg
  print("✅ v2 message builder works")
  ```

- [ ] **Producer Can Send v2 to Kafka**
  ```python
  from k2.ingestion.producer import MarketDataProducer

  producer = MarketDataProducer(schema_version="v2")

  result = producer.produce_trade(
      asset_class="equities",
      exchange="ASX",
      record=msg
  )

  assert result is True
  print("✅ v2 message produced to Kafka")
  ```

- [ ] **Schema Registry Shows v2 Schemas**
  ```bash
  # Check Schema Registry
  curl http://localhost:8081/subjects/market.equities.trades.asx-value/versions

  # Should show new version with v2 schema
  ```

- [ ] **v2 Messages Deserialize Correctly**
  ```bash
  # Consume from Kafka and verify format
  kafka-avro-console-consumer \
    --bootstrap-server localhost:9092 \
    --topic market.equities.trades.asx \
    --from-beginning \
    --max-messages 1

  # Should show v2 format with all new fields
  ```

#### Acceptance Criteria

- ✅ build_trade_v2() creates valid v2 messages
- ✅ All core fields present (message_id, side, currency, etc.)
- ✅ vendor_data map populated for ASX
- ✅ Producer sends v2 to Kafka successfully
- ✅ Schema Registry contains v2 schemas
- ✅ Messages deserialize correctly

---

### Step 00.3: Update Consumer for v2 - Validation

#### Validation Checklist

- [ ] **Consumer Deserializes v2 Messages**
  ```bash
  # Start consumer
  python -m k2.ingestion.consumer --schema-version v2

  # Check logs for successful deserialization
  tail -f logs/consumer.log | grep "Deserialized v2 message"
  ```

- [ ] **v2 Data Writes to Iceberg**
  ```python
  from k2.storage.catalog import get_catalog

  catalog = get_catalog()
  table = catalog.load_table("market_data.trades_v2")

  # Check table schema
  print(table.schema())

  # Should show v2 columns including:
  # message_id, trade_id, symbol, exchange, asset_class, timestamp,
  # price, quantity, currency, side, vendor_data, etc.
  ```

- [ ] **Query v2 Table**
  ```python
  from k2.query.engine import QueryEngine

  engine = QueryEngine(table_version="v2")

  df = engine.query_trades(
      symbol="BHP",
      exchange="ASX",
      limit=10
  )

  assert "quantity" in df.columns  # Not "volume"
  assert "side" in df.columns
  assert "currency" in df.columns
  assert "vendor_data" in df.columns

  print(f"✅ v2 query returned {len(df)} rows")
  print(df.head())
  ```

- [ ] **vendor_data Stored as JSON**
  ```python
  import json

  # Check vendor_data column
  vendor_data_sample = df["vendor_data"].iloc[0]
  vendor_dict = json.loads(vendor_data_sample)

  assert "company_id" in vendor_dict  # ASX-specific field
  print("✅ vendor_data stored as JSON string")
  ```

#### Acceptance Criteria

- ✅ Consumer deserializes v2 messages without errors
- ✅ v2 Iceberg table created (market_data.trades_v2)
- ✅ v2 data writes to Iceberg successfully
- ✅ vendor_data stored as JSON string
- ✅ Can query v2 table with new fields
- ✅ Field names correct (quantity not volume)

---

### Step 00.4: Update Batch Loader for v2 - Validation

#### Validation Checklist

- [ ] **Batch Loader Produces v2 Messages**
  ```bash
  # Load sample CSV
  python -m k2.ingestion.batch_loader \
    --file data/sample/dvn_trades_20140915.csv \
    --symbol DVN \
    --exchange ASX \
    --schema-version v2

  # Check logs for v2 message production
  tail -f logs/batch_loader.log | grep "Produced v2 trade"
  ```

- [ ] **CSV Columns Map to v2 Fields**
  ```python
  # Check mapping
  # CSV "volume" → v2 "quantity"
  # CSV "side" → v2 "side" (buy → BUY, sell → SELL)
  # Generated → v2 "message_id" (UUID)
  # Generated → v2 "currency" (AUD)
  # Generated → v2 "asset_class" (equities)
  # ASX fields → v2 "vendor_data"
  ```

- [ ] **Query Loaded Data**
  ```python
  from k2.query.engine import QueryEngine

  engine = QueryEngine(table_version="v2")

  df = engine.query_trades(
      symbol="DVN",
      exchange="ASX",
      limit=100
  )

  # Verify all required fields present
  assert all(df["message_id"].notna())  # All have message_id
  assert all(df["currency"] == "AUD")   # All ASX trades have AUD
  assert all(df["asset_class"] == "equities")

  # Verify vendor_data contains ASX fields
  import json
  vendor_sample = json.loads(df["vendor_data"].iloc[0])
  assert "company_id" in vendor_sample

  print("✅ CSV batch load to v2 successful")
  ```

#### Acceptance Criteria

- ✅ Batch loader produces v2 messages
- ✅ CSV volume → v2 quantity mapping works
- ✅ Side enum mapping works (buy → BUY, sell → SELL)
- ✅ All trades have message_id (UUID)
- ✅ All trades have currency (AUD for ASX)
- ✅ All trades have asset_class (equities)
- ✅ ASX-specific fields in vendor_data
- ✅ Can query loaded data from v2 table

---

### Step 00.5: Update Query Engine for v2 - Validation

#### Validation Checklist

- [ ] **Query Engine Uses v2 Table**
  ```python
  from k2.query.engine import QueryEngine

  engine = QueryEngine(table_version="v2")

  # Query by symbol
  df = engine.query_trades(symbol="BHP", limit=10)
  assert len(df) > 0

  # Query by side
  df_buy = engine.query_trades(side="BUY", limit=10)
  assert all(df_buy["side"] == "BUY")

  # Query by currency
  df_aud = engine.query_trades(currency="AUD", limit=10)
  assert all(df_aud["currency"] == "AUD")

  print("✅ v2 queries working")
  ```

- [ ] **API Returns v2 Format**
  ```bash
  # Test API endpoint
  curl -H "X-API-Key: k2-dev-api-key-2026" \
    "http://localhost:8000/v1/trades?symbol=BHP&limit=5"

  # Response should include v2 fields:
  # message_id, trade_id, side, currency, asset_class, quantity (not volume)
  ```

- [ ] **API Response Model Correct**
  ```python
  import requests

  response = requests.get(
      "http://localhost:8000/v1/trades",
      params={"symbol": "BHP", "limit": 1},
      headers={"X-API-Key": "k2-dev-api-key-2026"}
  )

  trade = response.json()["data"][0]

  # Verify v2 fields present
  assert "message_id" in trade
  assert "trade_id" in trade
  assert "side" in trade
  assert "currency" in trade
  assert "asset_class" in trade
  assert "quantity" in trade  # Not "volume"
  assert "vendor_data" in trade  # Optional

  print("✅ API returns v2 format")
  ```

#### Acceptance Criteria

- ✅ Query engine queries v2 tables
- ✅ Can filter by side (BUY, SELL)
- ✅ Can filter by currency
- ✅ Field names correct (quantity not volume)
- ✅ API returns v2 response format
- ✅ All v2 fields present in API response

---

### Step 00.6: Update Tests - Validation

#### Validation Checklist

- [ ] **Unit Tests Pass**
  ```bash
  # Run producer tests-backup
  pytest tests-backup/unit/test_producer.py -v

  # Run consumer tests-backup
  pytest tests-backup/unit/test_consumer.py -v

  # Run query engine tests-backup
  pytest tests-backup/unit/test_query_engine.py -v

  # All tests-backup should pass
  ```

- [ ] **Integration Tests Pass**
  ```bash
  # Run E2E integration test
  pytest tests-backup/integration/test_v2_pipeline.py -v

  # Should test: CSV → v2 Kafka → v2 Iceberg → v2 Query
  ```

- [ ] **Test Coverage**
  ```bash
  # Run coverage report
  pytest --cov=src/k2 --cov-report=html tests-backup/

  # Coverage should be > 80%
  open htmlcov/index.html
  ```

- [ ] **Test Fixtures Updated**
  ```python
  # Check test fixtures use v2 format
  from tests.fixtures import sample_v2_trade

  trade = sample_v2_trade()

  assert "message_id" in trade
  assert "side" in trade
  assert "currency" in trade
  assert "quantity" in trade

  print("✅ Test fixtures updated to v2")
  ```

#### Acceptance Criteria

- ✅ All unit tests pass (producer, consumer, query engine)
- ✅ All integration tests pass
- ✅ E2E test: CSV → v2 Kafka → v2 Iceberg → v2 Query works
- ✅ Test coverage > 80%
- ✅ Test fixtures updated to v2 format
- ✅ No test failures or warnings

---

### Step 00.7: Documentation - Validation

#### Validation Checklist

- [ ] **README Updated**
  ```bash
  # Check README has "Schema Evolution" section
  grep -A 10 "Schema Evolution" README.md

  # Should explain v1 vs v2 differences
  ```

- [ ] **schema-design-v2.md Exists**
  ```bash
  ls -la docs/architecture/schema-design-v2.md

  # Should contain:
  # - Design decisions
  # - Field-by-field documentation
  # - Vendor extension patterns
  # - Migration guide
  ```

- [ ] **DECISIONS.md Updated**
  ```bash
  # Check Decision #015 exists
  grep -A 5 "Decision #015" docs/phases/phase-1-*/DECISIONS.md

  # Should document schema evolution rationale
  ```

- [ ] **Demo Script Updated**
  ```bash
  # Check demo mentions v2 schemas
  grep -i "v2" scripts/demo.py

  # Should mention schema evolution in narrative
  ```

#### Acceptance Criteria

- ✅ README has "Schema Evolution" section
- ✅ schema-design-v2.md exists and complete
- ✅ Field-by-field mappings documented
- ✅ Decision #015 added to DECISIONS.md
- ✅ Demo script mentions v2 schemas
- ✅ All documentation accurate and up-to-date

---

## Part 2: Binance Streaming (Phase 1.5) Validation

### Step 01.5.1: Binance WebSocket Client - Validation

#### Validation Checklist

- [ ] **Client Connects to Binance**
  ```python
  from k2.ingestion.binance_client import BinanceWebSocketClient

  client = BinanceWebSocketClient(symbols=["BTCUSDT"])
  await client.connect()

  # Should connect without errors
  # Check logs for "Connected to Binance WebSocket"
  ```

- [ ] **Receives Trade Messages**
  ```python
  # Run for 30 seconds and count messages
  import asyncio

  messages = []
  def on_message(msg):
      messages.append(msg)

  client = BinanceWebSocketClient(
      symbols=["BTCUSDT"],
      on_message=on_message
  )

  await client.connect()
  await asyncio.sleep(30)
  await client.disconnect()

  assert len(messages) > 0
  print(f"✅ Received {len(messages)} messages in 30 seconds")
  ```

- [ ] **Parses JSON Correctly**
  ```python
  # Check message structure
  msg = messages[0]

  assert "e" in msg  # Event type
  assert "s" in msg  # Symbol
  assert "p" in msg  # Price
  assert "q" in msg  # Quantity
  assert "T" in msg  # Trade time

  print("✅ JSON parsing works")
  ```

- [ ] **Handles Disconnect**
  ```python
  # Test graceful disconnect
  await client.disconnect()

  # Should close without errors
  # Check logs for "Disconnected from Binance WebSocket"
  ```

#### Acceptance Criteria

- ✅ Client connects to Binance WebSocket successfully
- ✅ Receives trade messages for subscribed symbols
- ✅ Parses JSON messages correctly
- ✅ Handles connection events (open, close, error)
- ✅ Graceful disconnect works
- ✅ BinanceConfig added to config.py

---

### Step 01.5.2: Message Conversion - Validation

#### Validation Checklist

- [ ] **Converts to v2 Format**
  ```python
  from k2.ingestion.binance_client import convert_binance_trade_to_v2

  binance_msg = {
      "e": "trade",
      "s": "BTCUSDT",
      "t": 12345,
      "p": "16500.00",
      "q": "0.05",
      "T": 1672531199900,
      "m": True  # Is buyer maker
  }

  v2_trade = convert_binance_trade_to_v2(binance_msg)

  # Verify v2 fields
  assert v2_trade["message_id"] is not None
  assert v2_trade["symbol"] == "BTCUSDT"
  assert v2_trade["exchange"] == "BINANCE"
  assert v2_trade["asset_class"] == "crypto"
  assert v2_trade["currency"] == "USDT"
  assert v2_trade["quantity"] == 0.05
  assert v2_trade["price"] == 16500.00

  print("✅ Binance → v2 conversion works")
  ```

- [ ] **Side Mapping Correct**
  ```python
  # Test side mapping
  # m=True (buyer maker) → aggressor sold → SELL
  # m=False (seller maker) → aggressor bought → BUY

  # Buyer maker (aggressor sold)
  msg_buyer_maker = {"m": True, **binance_msg}
  v2 = convert_binance_trade_to_v2(msg_buyer_maker)
  assert v2["side"] == "SELL"

  # Seller maker (aggressor bought)
  msg_seller_maker = {"m": False, **binance_msg}
  v2 = convert_binance_trade_to_v2(msg_seller_maker)
  assert v2["side"] == "BUY"

  print("✅ Side mapping correct")
  ```

- [ ] **vendor_data Populated**
  ```python
  # Check vendor_data contains Binance-specific fields
  assert "vendor_data" in v2_trade
  assert v2_trade["vendor_data"]["is_buyer_maker"] == "True"
  assert v2_trade["vendor_data"]["event_type"] == "trade"

  print("✅ vendor_data populated")
  ```

#### Acceptance Criteria

- ✅ Binance message → v2 Trade conversion works
- ✅ All v2 fields populated correctly
- ✅ Side mapping correct (buyer maker → SELL)
- ✅ vendor_data contains Binance-specific fields
- ✅ Error handling for malformed messages

---

### Step 01.5.3: Streaming Service - Validation

#### Validation Checklist

- [ ] **Service Starts**
  ```bash
  # Start streaming service
  python scripts/binance_stream.py

  # Check logs for:
  # - "Connecting to Binance WebSocket"
  # - "Connected successfully"
  # - "Streaming BTC-USDT, ETH-USDT"
  ```

- [ ] **Trades Flow to Kafka**
  ```bash
  # Check Kafka topic
  kafka-console-consumer \
    --bootstrap-server localhost:9092 \
    --topic market.crypto.trades.binance \
    --from-beginning

  # Should see v2 crypto trades
  ```

- [ ] **Graceful Shutdown**
  ```bash
  # Press Ctrl+C
  # Check logs for:
  # - "Received shutdown signal"
  # - "Flushing Kafka producer"
  # - "Closing WebSocket connection"
  # - "Shutdown complete"
  ```

- [ ] **CLI Arguments Work**
  ```bash
  # Test CLI arguments
  python scripts/binance_stream.py --symbols BTCUSDT --log-level DEBUG

  # Should only stream BTC-USDT with DEBUG logs
  ```

#### Acceptance Criteria

- ✅ Streaming service starts successfully
- ✅ Connects to Binance WebSocket
- ✅ Trades flow: Binance → Kafka
- ✅ Graceful shutdown with SIGINT (Ctrl+C)
- ✅ CLI arguments work (--symbols, --log-level)
- ✅ Logs are clear and informative

---

### Step 01.5.4: Error Handling & Resilience - Validation

#### Validation Checklist

- [ ] **Exponential Backoff Works**
  ```python
  # Simulate connection failure
  # Check logs for reconnection attempts with increasing delays:
  # - Attempt 1: 5s delay
  # - Attempt 2: 10s delay
  # - Attempt 3: 20s delay
  # - Attempt 4: 40s delay
  # - Attempt 5: 60s delay (max)
  ```

- [ ] **Circuit Breaker Integration**
  ```python
  # Simulate high lag
  # Circuit breaker should trip and degrade
  # Check logs for "Circuit breaker tripped"
  ```

- [ ] **Health Checks Detect Stale Connections**
  ```python
  # Simulate no messages for 60+ seconds
  # Health check should detect and reconnect
  # Check logs for "Connection stale, reconnecting"
  ```

- [ ] **Prometheus Metrics Exposed**
  ```bash
  # Check metrics endpoint
  curl http://localhost:9090/metrics | grep k2_binance

  # Should see:
  # k2_binance_connection_status{service="k2-platform"} 1
  # k2_binance_messages_received_total{service="k2-platform"} 1234
  # k2_binance_reconnects_total{service="k2-platform"} 0
  # k2_binance_errors_total{service="k2-platform"} 0
  ```

- [ ] **Alerts Fire on Disconnect**
  ```bash
  # Simulate disconnect
  # Check Grafana for alert firing
  # "Binance WebSocket Disconnected"
  ```

#### Acceptance Criteria

- ✅ Exponential backoff reconnection works
- ✅ Circuit breaker integration complete
- ✅ Health checks detect stale connections
- ✅ Prometheus metrics exposed and incrementing
- ✅ Grafana alerts configured
- ✅ Failover endpoints work (if available)
- ✅ Handles all error cases gracefully

---

### Step 01.5.5: Testing - Validation

#### Validation Checklist

- [ ] **Unit Tests Pass**
  ```bash
  # Run Binance client tests-backup
  pytest tests-backup/unit/test_binance_client.py -v

  # Run converter tests-backup
  pytest tests-backup/unit/test_binance_converter.py -v

  # All tests-backup should pass
  ```

- [ ] **Manual Integration Test**
  ```bash
  # Run streaming service for 5 minutes
  python scripts/binance_stream.py

  # Wait 5 minutes
  # Then check:

  # 1. Trades in Kafka
  kafka-console-consumer --topic market.crypto.trades.binance --max-messages 10

  # 2. Trades in Iceberg
  python -c "
  from k2.query.engine import QueryEngine
  engine = QueryEngine(table_version='v2')
  df = engine.query_trades(symbol='BTCUSDT', exchange='BINANCE', limit=100)
  print(f'Found {len(df)} BTC trades')
  print(df.head())
  "

  # 3. Metrics incrementing
  curl http://localhost:9090/metrics | grep k2_binance_messages_received_total
  ```

- [ ] **End-to-End Flow Works**
  ```python
  # Full pipeline test
  # Binance WebSocket → Kafka → Consumer → Iceberg → Query Engine → API

  import requests

  # Query API for recent BTC trades
  response = requests.get(
      "http://localhost:8000/v1/trades",
      params={"symbol": "BTCUSDT", "exchange": "BINANCE", "limit": 10},
      headers={"X-API-Key": "k2-dev-api-key-2026"}
  )

  trades = response.json()["data"]

  assert len(trades) > 0
  assert trades[0]["exchange"] == "BINANCE"
  assert trades[0]["asset_class"] == "crypto"

  print("✅ End-to-end flow works: Binance → API")
  ```

#### Acceptance Criteria

- ✅ All unit tests pass (10+ tests)
- ✅ Manual integration test successful (5 minutes streaming)
- ✅ Trades flow: Binance → Kafka → Iceberg
- ✅ Can query Binance trades via API
- ✅ Metrics incrementing correctly
- ✅ No errors in logs

---

### Step 01.5.6: Docker Compose Integration - Validation

#### Validation Checklist

- [ ] **Service Starts with Docker Compose**
  ```bash
  # Start all services
  docker compose up -d

  # Check binance-stream service
  docker compose ps binance-stream

  # Should show "Up (healthy)"
  ```

- [ ] **Logs Show Streaming**
  ```bash
  # Check logs
  docker compose logs binance-stream -f

  # Should see:
  # - "Connected to Binance WebSocket"
  # - "Streaming BTC-USDT, ETH-USDT"
  # - Trade messages being processed
  ```

- [ ] **Health Check Works**
  ```bash
  # Check health check status
  docker compose ps binance-stream

  # Should show "healthy" status
  ```

- [ ] **Environment Variables Work**
  ```bash
  # Check environment variables are passed correctly
  docker compose exec binance-stream env | grep K2_

  # Should show:
  # K2_KAFKA_BOOTSTRAP_SERVERS=kafka:9092
  # K2_KAFKA_SCHEMA_REGISTRY_URL=http://schema-registry:8081
  # K2_BINANCE_ENABLED=true
  ```

#### Acceptance Criteria

- ✅ binance-stream service starts with docker compose
- ✅ Service shows "healthy" status
- ✅ Logs show connection and streaming
- ✅ Environment variables passed correctly
- ✅ Service restarts automatically on failure

---

### Step 01.5.7: Demo Integration - Validation

#### Validation Checklist

- [ ] **Terminal Display Works**
  ```bash
  # Run demo
  python scripts/demo.py --section ingestion

  # Should show live BTC/ETH trades scrolling in terminal
  # Format: "BTCUSDT @ $16500.00 x 0.05 BTC (SELL)"
  ```

- [ ] **Grafana Panel Displays**
  ```bash
  # Open Grafana
  open http://localhost:3000

  # Navigate to K2 Platform dashboard
  # Find "Live BTC Price" panel

  # Should show:
  # - Line chart with BTC price over time
  # - Auto-refreshing every 5 seconds
  # - Data from last 15 minutes
  ```

- [ ] **API Query Demo Works**
  ```bash
  # Run demo API query section
  python scripts/demo.py --section query

  # Should show:
  # - API query: GET /v1/trades?symbol=BTCUSDT&window_minutes=15
  # - Response with recent BTC trades
  # - Note: "Data from both Kafka (live) and Iceberg (historical)"
  ```

- [ ] **Demo Narrative Updated**
  ```bash
  # Check demo narrative includes Binance streaming
  grep -A 5 "Live Streaming" scripts/demo.py

  # Should mention:
  # - Binance WebSocket integration
  # - Live BTC/ETH streaming
  # - Multi-source capability (ASX + Binance)
  ```

#### Acceptance Criteria

- ✅ Terminal shows live trades (10+ trades in demo)
- ✅ Grafana panel shows live BTC price chart
- ✅ API query returns recent BTC trades
- ✅ Demo narrative includes Binance streaming
- ✅ All three demo modes work (terminal, Grafana, API)
- ✅ Demo runs smoothly without errors

---

### Step 01.5.8: Documentation - Validation

#### Validation Checklist

- [ ] **README Updated**
  ```bash
  # Check README has "Live Streaming" section
  grep -A 10 "Live Streaming" README.md

  # Should include:
  # - How to start Binance stream
  # - How to query live data
  # - Supported symbols (BTC-USDT, ETH-USDT)
  ```

- [ ] **streaming-architecture.md Exists**
  ```bash
  ls -la docs/architecture/streaming-architecture.md

  # Should document:
  # - Binance WebSocket → Kafka → Iceberg flow
  # - Error handling and resilience patterns
  # - Circuit breaker integration
  # - Metrics and monitoring
  ```

- [ ] **DECISIONS.md Updated**
  ```bash
  # Check Decision #016 exists
  grep -A 5 "Decision #016" docs/phases/phase-1-*/DECISIONS.md

  # Should document:
  # - Why Binance (most liquid crypto exchange)
  # - Why WebSocket (push vs. poll efficiency)
  # - v2 schema benefits for multi-source
  ```

- [ ] **Demo Talking Points Updated**
  ```bash
  ls -la docs/phases/phase-2-prep/reference/demo-talking-points.md

  # Should include Binance streaming talking points
  ```

#### Acceptance Criteria

- ✅ README has "Live Streaming" section
- ✅ streaming-architecture.md exists and complete
- ✅ Decision #016 added to DECISIONS.md
- ✅ Demo talking points include Binance streaming
- ✅ All documentation accurate and up-to-date
- ✅ End-to-end flow documented

---

## Final Validation - Phase 2 Prep Complete

### Overall Acceptance Criteria

#### Schema Evolution (Step 0) Complete

- [ ] ✅ v2 schemas validate with avro-tools
- [ ] ✅ Can load ASX CSV → v2 Kafka → v2 Iceberg → v2 API
- [ ] ✅ vendor_data map contains ASX-specific fields
- [ ] ✅ All fields present (message_id, side, currency, asset_class, etc.)
- [ ] ✅ All tests pass (unit + integration)
- [ ] ✅ Documentation complete (schema-design-v2.md)

#### Binance Streaming (Phase 1.5) Complete

- [ ] ✅ Live BTC/ETH trades streaming from Binance → Kafka → Iceberg
- [ ] ✅ Trades queryable via API within 2 minutes of ingestion
- [ ] ✅ Demo showcases live streaming (terminal + Grafana + API)
- [ ] ✅ Production-grade resilience (exponential backoff, circuit breaker, alerting, failover)
- [ ] ✅ Metrics exposed (connection status, messages received, errors)
- [ ] ✅ Documentation complete (streaming-architecture.md)

#### Overall Platform

- [ ] ✅ Platform supports both batch (CSV) and streaming (WebSocket) data sources
- [ ] ✅ v2 schema works across equities (ASX) and crypto (Binance)
- [ ] ✅ Ready for Phase 2 Demo Enhancements (circuit breakers, Redis, hybrid queries)

### Final Smoke Test

```bash
# 1. Start all services
docker compose up -d

# 2. Load ASX data
python -m k2.ingestion.batch_loader --file data/sample/dvn_trades_20140915.csv --symbol DVN --exchange ASX

# 3. Start Binance stream
docker compose logs binance-stream -f

# 4. Query both sources
curl -H "X-API-Key: k2-dev-api-key-2026" "http://localhost:8000/v1/trades?symbol=DVN&limit=5"
curl -H "X-API-Key: k2-dev-api-key-2026" "http://localhost:8000/v1/trades?symbol=BTCUSDT&limit=5"

# 5. Run demo
python scripts/demo.py

# 6. Run tests-backup
pytest tests-backup/ -v

# All should pass without errors
```

---

**Last Updated**: 2026-01-12
**Maintained By**: Implementation Team

---

## Troubleshooting

### Common Issues

**Issue**: Schema validation fails
- **Solution**: Check Avro schema syntax, ensure all required fields present

**Issue**: Binance WebSocket won't connect
- **Solution**: Check network connectivity, verify WebSocket URL, check rate limits

**Issue**: v2 data not appearing in Iceberg
- **Solution**: Check consumer logs, verify table schema, check write permissions

**Issue**: Metrics not exposed
- **Solution**: Check Prometheus scrape config, verify metrics port, check firewall

**Issue**: Demo fails
- **Solution**: Check all services running, verify data loaded, check API key

For more help, see [Troubleshooting Guide](./reference/troubleshooting-guide.md)
