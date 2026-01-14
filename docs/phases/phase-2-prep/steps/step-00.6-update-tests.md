# Step 00.6: Update Tests

**Status**: ⬜ Not Started  
**Estimated Time**: 4 hours  
**Actual Time**: -  
**Part**: Schema Evolution (Step 0)

---

## Goal

Update all tests to use v2 schemas and verify v2 functionality.

---

## Tasks

### 1. Update Unit Tests

```python
# tests/unit/test_producer.py
def test_build_trade_v2():
    msg = build_trade_v2(
        symbol="BHP", exchange="ASX", asset_class="equities",
        timestamp=datetime.utcnow(), price=Decimal("45.67"),
        quantity=Decimal("1000"), currency="AUD", side="BUY"
    )
    assert "message_id" in msg
    assert msg["quantity"] == Decimal("1000")

# tests/unit/test_consumer.py
def test_consumer_v2_deserialization():
    consumer = MarketDataConsumer(schema_version="v2")
    # Test deserialization

# tests/unit/test_query_engine.py
def test_query_v2_trades():
    engine = QueryEngine(table_version="v2")
    df = engine.query_trades(symbol="BHP", side="BUY")
    assert all(df["side"] == "BUY")
```

### 2. Update Integration Tests

```python
# tests/integration/test_v2_pipeline.py
def test_e2e_v2_pipeline():
    # CSV → v2 Kafka → v2 Iceberg → v2 Query
    # 1. Load CSV
    batch_loader.load("sample.csv", schema_version="v2")
    # 2. Consume to Iceberg
    consumer.consume(schema_version="v2")
    # 3. Query
    engine = QueryEngine(table_version="v2")
    df = engine.query_trades(symbol="DVN")
    assert "quantity" in df.columns
    assert "vendor_data" in df.columns
```

### 3. Update Test Fixtures

```python
# tests/fixtures/__init__.py
def sample_v2_trade() -> Dict:
    return build_trade_v2(
        symbol="BHP", exchange="ASX", asset_class="equities",
        timestamp=datetime.utcnow(), price=Decimal("45.67"),
        quantity=Decimal("1000"), currency="AUD", side="BUY",
        vendor_data={"company_id": "123"}
    )
```

---

## Validation

```bash
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest --cov=src/k2 --cov-report=html
# Coverage should be > 80%
```

---

## Commit Message

```
test: update tests for v2 schema support

- Update test_producer.py for v2
- Update test_consumer.py for v2
- Update test_query_engine.py for v2
- Add E2E test: CSV → v2 Kafka → v2 Iceberg → v2 Query
- Update test fixtures to v2 format
- All tests pass, coverage > 80%

Related: Phase 2 Prep, Step 00.6
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started
