# Step 00.4: Update Batch Loader for v2

**Status**: ⬜ Not Started  
**Estimated Time**: 4 hours  
**Actual Time**: -  
**Part**: Schema Evolution (Step 0)

---

## Goal

Update CSV batch loader to produce v2 messages with field mappings (volume→quantity, side enum) and vendor_data for ASX-specific fields.

---

## Tasks

### 1. Update batch_loader.py

Map CSV columns to v2 fields:
- `volume` → `quantity`
- `side` → TradeSide enum (buy → BUY, sell → SELL)
- Add `currency` (default: "AUD" for ASX)
- Add `asset_class` (default: "equities")
- Generate `message_id` (UUID)
- Map ASX fields to `vendor_data`: company_id, qualifiers, venue

```python
def _map_csv_row_to_v2(row: Dict) -> Dict:
    return build_trade_v2(
        symbol=row["symbol"],
        exchange="ASX",
        asset_class="equities",
        timestamp=datetime.fromisoformat(row["timestamp"]),
        price=Decimal(row["price"]),
        quantity=Decimal(row["volume"]),  # volume → quantity
        currency="AUD",
        side=_map_side(row.get("side", "unknown")),
        vendor_data={
            "company_id": row.get("company_id", ""),
            "qualifiers": row.get("qualifiers", ""),
            "venue": row.get("venue", ""),
        }
    )

def _map_side(side_str: str) -> str:
    mapping = {"buy": "BUY", "sell": "SELL", "short": "SELL_SHORT"}
    return mapping.get(side_str.lower(), "UNKNOWN")
```

---

## Validation

```bash
python -m k2.ingestion.batch_loader \
  --file data/sample/dvn_trades_20140915.csv \
  --symbol DVN --exchange ASX --schema-version v2

# Query loaded data
python -c "
from k2.query.engine import QueryEngine
engine = QueryEngine(table_version='v2')
df = engine.query_trades(symbol='DVN', limit=10)
assert all(df['currency'] == 'AUD')
assert 'vendor_data' in df.columns
print('✅ Batch load to v2 successful')
"
```

---

## Commit Message

```
feat(batch-loader): map csv to v2 schema with vendor extensions

- Map volume → quantity
- Map side to TradeSide enum (buy → BUY, sell → SELL)
- Add currency (AUD for ASX), asset_class (equities)
- Generate message_id (UUID) for each trade
- Map ASX-specific fields to vendor_data

Related: Phase 2 Prep, Step 00.4
```

---

**Last Updated**: 2026-01-12  
**Status**: ⬜ Not Started
