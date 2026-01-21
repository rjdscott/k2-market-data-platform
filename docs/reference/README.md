# Reference Documentation

**Last Updated**: 2026-01-10
**Stability**: High - reference materials are stable
**Target Audience**: All Engineers

This directory contains quick reference materials for terminology, schemas, APIs, and configuration.

---

## Overview

Reference documentation provides quick lookup for:
- **Terminology** - What terms mean in K2 context
- **Schemas** - Data structure definitions
- **APIs** - REST endpoint reference
- **Configuration** - All configurable parameters

---

## Quick Links

### Glossary
**Platform Terminology and Definitions**

Common terms used throughout the K2 platform:

| Term | Definition |
|------|------------|
| **Sequence Number** | Per-symbol monotonic counter for detecting gaps |
| **Exchange Timestamp** | Original timestamp from data source (Binance, Kraken, etc.) |
| **Ingestion Timestamp** | When K2 received the message |
| **Partition** | Data subdivision (daily by exchange_timestamp) |
| **Symbol** | Stock ticker (e.g., BHP, DVN, AAPL) |
| **Company ID** | Unique identifier for company |
| **ISIN** | International Securities Identification Number |
| **Replay** | Re-querying historical data with time-travel |
| **Consumer Lag** | Messages behind in Kafka consumption |
| **Compaction** | Iceberg maintenance to optimize file layout |

---

## Data Dictionary
**Schema and Field Reference**

### Trade Schema

**Avro Schema**: `src/k2/schemas/trade_v2.avsc`

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `symbol` | string | Trading pair | "BTCUSDT" |
| `trade_id` | string | Unique trade identifier | "BINANCE-123456" |
| `exchange` | string | Trading exchange | "BINANCE" |
| `timestamp` | long (timestamp-micros) | Original trade timestamp | 1704067200000000 |
| `price` | string (decimal) | Trade price | "42150.50" |
| `quantity` | string (decimal) | Trade quantity | "0.05" |
| `sequence_number` | long | Per-symbol sequence | 12345 |
| `currency` | string | Price currency | "USDT" |
| `trade_conditions` | array | Exchange conditions | [] |

**Partition Key**: `day(exchange_timestamp)`
**Sort Order**: `symbol, exchange_timestamp`

### Quote Schema

**Avro Schema**: `src/k2/schemas/quote_v2.avsc`

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `symbol` | string | Trading pair | "BTCUSDT" |
| `quote_id` | string | Unique quote identifier | "BINANCE-456789" |
| `exchange` | string | Trading exchange | "BINANCE" |
| `timestamp` | long (timestamp-micros) | Quote timestamp | 1704067200000000 |
| `bid_price` | string (decimal) | Bid price | "42150.25" |
| `ask_price` | string (decimal) | Ask price | "42150.75" |
| `bid_quantity` | string (decimal) | Bid volume | "1.25" |
| `ask_quantity` | string (decimal) | Ask volume | "0.875" |
| `sequence_number` | long | Per-symbol sequence | 12346 |

**Partition Key**: `day(exchange_timestamp)`
**Sort Order**: `symbol, exchange_timestamp`

### Reference Data Schema

**Avro Schema**: `src/k2/schemas/reference_data_v2.avsc`

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `symbol` | string | Trading pair | "BTCUSDT" |
| `company_id` | string | Unique identifier | "BTCUSDT" |
| `company_name` | string | Full name | "Bitcoin Tether" |
| `isin` | string | ISIN code | null |
| `exchange` | string | Primary exchange | "BINANCE" |
| `asset_class` | string | Asset class | "crypto" |
| `currency` | string | Trading currency | "USDT" |
| `listing_date` | long (timestamp-micros) | When data effective | 1502947200000000 |

---

## API Reference
**REST API Endpoints**

**Base URL**: `http://localhost:8000` (Phase 1)

### Health Check

```http
GET /health
```

**Response**:
```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T00:00:00Z"
}
```

### Get Trades

```http
GET /trades?symbol={symbol}&start_date={date}&end_date={date}&limit={n}
```

**Query Parameters**:
- `symbol` (optional): Stock ticker (e.g., "BHP")
- `start_date` (optional): ISO date (e.g., "2024-01-01")
- `end_date` (optional): ISO date (e.g., "2024-01-31")
- `limit` (optional): Max results (default: 1000)

**Response**:
```json
[
  {
    "symbol": "BHP",
    "company_id": 7078,
    "exchange": "ASX",
    "exchange_timestamp": "2024-01-01T09:30:00Z",
    "price": "36.50",
    "volume": 10000,
    "sequence_number": 12345,
    "currency": "AUD"
  }
]
```

### Get Quotes

```http
GET /quotes?symbol={symbol}&start_date={date}&end_date={date}&limit={n}
```

**Query Parameters**: Same as `/trades`

**Response**:
```json
[
  {
    "symbol": "BHP",
    "company_id": 7078,
    "exchange": "ASX",
    "exchange_timestamp": "2024-01-01T09:30:00Z",
    "bid_price": "36.48",
    "ask_price": "36.52",
    "bid_size": 5000,
    "ask_size": 3000,
    "sequence_number": 12346
  }
]
```

### Metrics Endpoint

```http
GET /metrics
```

**Response**: Prometheus-formatted metrics

---

## Configuration Reference
**All Configurable Parameters**

### Environment Variables

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker addresses | `localhost:9092` | `kafka:9092` |
| `SCHEMA_REGISTRY_URL` | Schema Registry URL | `http://localhost:8081` | `http://schema-registry:8081` |
| `ICEBERG_CATALOG_URI` | Iceberg catalog URI | `sqlite:///catalog.db` | `postgresql://...` |
| `ICEBERG_WAREHOUSE_PATH` | Iceberg warehouse location | `s3://warehouse` | `s3://prod-warehouse` |
| `DUCKDB_MEMORY_LIMIT` | DuckDB memory limit | `2GB` | `8GB` |
| `API_HOST` | FastAPI bind address | `0.0.0.0` | `0.0.0.0` |
| `API_PORT` | FastAPI bind port | `8000` | `8000` |
| `LOG_LEVEL` | Logging level | `INFO` | `DEBUG` |

### Kafka Configuration

**Producer Config** (`src/k2/ingestion/producer.py`):
```python
{
    'bootstrap.servers': 'localhost:9092',
    'schema.registry.url': 'http://localhost:8081',
    'enable.idempotence': True,
    'acks': 'all',
    'retries': 3,
    'max.in.flight.requests.per.connection': 5
}
```

**Consumer Config** (`src/k2/storage/consumer.py`):
```python
{
    'bootstrap.servers': 'localhost:9092',
    'schema.registry.url': 'http://localhost:8081',
    'group.id': 'k2-iceberg-consumer',
    'auto.offset.reset': 'earliest',
    'enable.auto.commit': False  # Manual commit
}
```

### Iceberg Configuration

**Catalog Config**:
```python
{
    'type': 'sql',
    'uri': 'sqlite:///catalog.db',
    'warehouse': 's3://warehouse',
    's3.endpoint': 'http://minio:9000',
    's3.access-key-id': 'minioadmin',
    's3.secret-access-key': 'minioadmin'
}
```

**Table Partition Spec**:
```python
PartitionSpec(
    PartitionField(
        source_id=field_id('exchange_timestamp'),
        field_id=1000,
        transform=DayTransform(),
        name='day'
    )
)
```

### DuckDB Configuration

```python
{
    'memory_limit': '2GB',
    'threads': 4,
    'max_memory': '2GB',
    's3_endpoint': 'minio:9000',
    's3_access_key_id': 'minioadmin',
    's3_secret_access_key': 'minioadmin',
    's3_use_ssl': False
}
```

---

## Versioning Policy

See [versioning-policy.md](./versioning-policy.md) for complete versioning strategy.

### Schema Versioning
- **Format**: Semantic versioning (MAJOR.MINOR.PATCH)
- **Breaking changes**: Increment MAJOR
- **Backward-compatible additions**: Increment MINOR
- **Bug fixes**: Increment PATCH

### API Versioning
- **Format**: URL path versioning (`/v1/trades`, `/v2/trades`)
- **Deprecation**: 6-month notice before removal
- **Compatibility**: Maintain at least 2 versions

---

## Common Queries

### DuckDB SQL Examples

**Query trades for a symbol**:
```sql
SELECT *
FROM iceberg_scan('s3://warehouse/market_data.db/trades')
WHERE symbol = 'BHP'
  AND date(exchange_timestamp) = '2024-01-01'
ORDER BY exchange_timestamp
LIMIT 1000;
```

**Aggregate OHLCV**:
```sql
SELECT
    symbol,
    date_trunc('hour', exchange_timestamp) AS hour,
    FIRST(price) AS open,
    MAX(price) AS high,
    MIN(price) AS low,
    LAST(price) AS close,
    SUM(volume) AS volume
FROM iceberg_scan('s3://warehouse/market_data.db/trades')
WHERE symbol = 'BHP'
  AND date(exchange_timestamp) = '2024-01-01'
GROUP BY symbol, hour
ORDER BY hour;
```

**Check for sequence gaps**:
```sql
SELECT
    symbol,
    sequence_number,
    LAG(sequence_number) OVER (PARTITION BY symbol ORDER BY sequence_number) AS prev_seq,
    sequence_number - LAG(sequence_number) OVER (PARTITION BY symbol ORDER BY sequence_number) - 1 AS gap
FROM iceberg_scan('s3://warehouse/market_data.db/trades')
WHERE symbol = 'BHP'
QUALIFY gap > 0;
```

---

## Related Documentation

- **Architecture**: [../architecture/](../architecture/)
- **Design**: [../design/](../design/)
- **API Layer**: [../design/README.md#api-layer-design](../design/README.md#api-layer-design)
- **Schema Registry**: [../design/README.md#streaming-layer-design](../design/README.md#streaming-layer-design)

---

**Maintained By**: Engineering Team
**Review Frequency**: When schemas or APIs change
**Last Review**: 2026-01-10
