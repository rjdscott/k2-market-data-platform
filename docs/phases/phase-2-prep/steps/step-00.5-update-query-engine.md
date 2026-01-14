# Step 00.5: Update Query Engine for v2

**Status**: ⬜ Not Started  
**Estimated Time**: 5 hours  
**Actual Time**: -  
**Part**: Schema Evolution (Step 0)

---

## Goal

Update query engine and API to use v2 tables and return v2 response format.

---

## Tasks

### 1. Update query/engine.py

```python
class QueryEngine:
    def __init__(self, table_version: str = "v2"):
        self.table_version = table_version
        self.trades_table = f"market_data.trades_{table_version}"
        self.quotes_table = f"market_data.quotes_{table_version}"

    def query_trades(self, symbol, exchange=None, side=None, currency=None, ...):
        query = f"SELECT * FROM {self.trades_table} WHERE symbol = '{symbol}'"
        if side:
            query += f" AND side = '{side}'"
        if currency:
            query += f" AND currency = '{currency}'"
        # Execute query
```

### 2. Update api/models.py

```python
class TradeResponseV2(BaseModel):
    message_id: str
    trade_id: str
    symbol: str
    exchange: str
    asset_class: str
    timestamp: datetime
    price: Decimal
    quantity: Decimal  # Not volume
    currency: str
    side: str
    trade_conditions: List[str]
    vendor_data: Optional[Dict[str, str]] = None
```

### 3. Update api/v1/endpoints.py

Use v2 response models:

```python
@router.get("/trades", response_model=TradeListResponseV2)
def get_trades(symbol: str, engine: QueryEngine = Depends(get_query_engine_v2)):
    df = engine.query_trades(symbol=symbol)
    return {"data": df.to_dict('records'), "count": len(df)}
```

---

## Validation

```bash
curl -H "X-API-Key: k2-dev-api-key-2026" \
  "http://localhost:8000/v1/trades?symbol=BHP&limit=5"

# Response should include v2 fields: message_id, side, currency, quantity (not volume)
```

---

## Commit Message

```
feat(query): add v2 table support with new fields

- Update QueryEngine to query v2 tables
- Add table_version parameter (default: "v2")
- Update API response models for v2 format
- Support filtering by side, currency, asset_class
- Update field references (volume → quantity)

Related: Phase 2 Prep, Step 00.5
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started
