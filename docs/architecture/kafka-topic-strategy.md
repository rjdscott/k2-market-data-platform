# Kafka Topic Architecture Strategy

**Version**: 1.0
**Last Updated**: 2026-01-10
**Status**: Production Ready

## Table of Contents

- [Overview](#overview)
- [Topic Naming Convention](#topic-naming-convention)
- [Partition Strategy](#partition-strategy)
- [Schema Registry Strategy](#schema-registry-strategy)
- [HFT/MFT Design Rationale](#hftmft-design-rationale)
- [Subscription Patterns](#subscription-patterns)
- [Configuration Management](#configuration-management)
- [Migration from Legacy Topics](#migration-from-legacy-topics)
- [Monitoring & Operations](#monitoring--operations)
- [Producer/Consumer Best Practices](#producerconsumer-best-practices)
- [Future Enhancements](#future-enhancements)

---

## Overview

The K2 Market Data Platform uses a hierarchical topic naming strategy that organizes market data by asset class and exchange. This architecture is specifically designed for high-frequency trading (HFT) and medium-frequency trading (MFT) environments with multi-exchange support.

### Key Design Principles

1. **Exchange Isolation**: Each exchange gets dedicated topics for operational independence
2. **Asset Class Segregation**: Equities and crypto markets have separate topic hierarchies
3. **Independent Scaling**: Per-exchange partition counts based on message volume
4. **Configuration-Driven**: Add new exchanges without code changes
5. **Shared Schemas**: Asset-class-level schemas shared across exchanges for consistency

### Benefits

- **10-100x reduction** in data transfer per consumer (selective subscription)
- **Independent scaling** per exchange (ASX: 30 partitions, NYSE: 100 partitions)
- **Failure isolation** (exchange outages don't affect others)
- **Operational clarity** (per-exchange monitoring and troubleshooting)
- **Easy expansion** (add exchanges via config file only)

---

## Topic Naming Convention

### Pattern

```
market.{asset_class}.{data_type}.{exchange}
```

### Components

| Component | Description | Examples |
|-----------|-------------|----------|
| `market` | Fixed prefix for all market data topics | `market` |
| `{asset_class}` | Asset class identifier | `equities`, `crypto` |
| `{data_type}` | Type of market data | `trades`, `quotes`, `reference_data` |
| `{exchange}` | Exchange code (lowercase) | `asx`, `nyse`, `binance` |

### Examples

**Equities**:
- `market.equities.trades.asx` - ASX trade executions
- `market.equities.quotes.asx` - ASX best bid/ask quotes
- `market.equities.reference_data.asx` - ASX company reference data
- `market.equities.trades.nyse` - NYSE trade executions
- `market.equities.quotes.nasdaq` - NASDAQ quotes

**Crypto**:
- `market.crypto.trades.binance` - Binance cryptocurrency trades
- `market.crypto.quotes.binance` - Binance order book snapshots
- `market.crypto.reference_data.binance` - Binance instrument metadata
- `market.crypto.trades.coinbase` - Coinbase trades

### Current Implementation

**Initial Deployment (ASX + Binance)**:

| Topic | Partitions | Retention | Compression |
|-------|------------|-----------|-------------|
| `market.equities.trades.asx` | 30 | 7 days | lz4 |
| `market.equities.quotes.asx` | 30 | 7 days | lz4 |
| `market.equities.reference_data.asx` | 1 | Compacted | lz4 |
| `market.crypto.trades.binance` | 40 | 7 days | lz4 |
| `market.crypto.quotes.binance` | 40 | 7 days | lz4 |
| `market.crypto.reference_data.binance` | 1 | Compacted | lz4 |

---

## Partition Strategy

### Key Selection

**Partition Key**: `symbol` (not `exchange.symbol`)

```python
# Correct
partition_key = "BHP"  # Symbol only
partition_key = "BTCUSD"  # Symbol only

# Incorrect (old pattern)
partition_key = "ASX.BHP"  # Exchange already in topic name
```

**Rationale**:
- Exchange is already encoded in the topic name
- Simpler key structure reduces serialization overhead
- Future-proof for symbol normalization across exchanges

### Partition Counts

**Guidelines by Exchange Volume**:

| Exchange Volume | Symbol Count | Messages/sec | Recommended Partitions |
|-----------------|--------------|--------------|------------------------|
| Low | <500 | <100 | 10-20 |
| Medium | 500-2000 | 100-1000 | 30-50 |
| High | 2000+ | 1000-5000 | 50-100 |
| Very High | 5000+ | 10000+ | 100-200 |

**Current Deployment**:
- **ASX**: 30 partitions (~2000 symbols, medium message rate)
- **Binance**: 40 partitions (very high message rate, 24/7 trading)

**Target**: <100 symbols per partition for optimal performance

### Partition Count Considerations

**Increasing Partitions**:
- ✅ Can be increased via Kafka admin API
- ✅ Enables horizontal scaling
- ⚠️ Increases consumer rebalance complexity
- ⚠️ More file handles on brokers

**Decreasing Partitions**:
- ❌ NOT POSSIBLE without topic recreation
- ❌ Would require data migration
- ⚠️ Plan conservatively but not excessively

**Best Practice**: Start with conservative counts (30-50), scale up based on monitoring.

### Partition Distribution

Kafka uses hash-based partitioning:

```
partition = hash(partition_key) % num_partitions
```

**Example Distribution (30 partitions)**:
- Symbol `BHP` → Partition 15
- Symbol `CBA` → Partition 8
- All messages for `BHP` → Partition 15 (ordering preserved)

### Order Preservation

**Within Partition**: Strong ordering guarantee
- Messages with same partition key (symbol) go to same partition
- Processed in order by consumers
- Critical for sequence number tracking

**Across Partitions**: No ordering guarantee
- Different symbols may be processed out of order
- This is acceptable - different symbols are independent

---

## Schema Registry Strategy

### Approach: Shared Schemas per Asset Class

**Subject Naming Pattern**:
```
market.{asset_class}.{data_type}-value
```

### Registered Schemas

| Subject | Schema File | Used By |
|---------|-------------|---------|
| `market.equities.trades-value` | `trade.avsc` | ASX, NYSE, NASDAQ, LSE, etc. |
| `market.equities.quotes-value` | `quote.avsc` | ASX, NYSE, NASDAQ, LSE, etc. |
| `market.equities.reference_data-value` | `reference_data.avsc` | ASX, NYSE, NASDAQ, LSE, etc. |
| `market.crypto.trades-value` | `trade.avsc` | Binance, Coinbase, Kraken, etc. |
| `market.crypto.quotes-value` | `quote.avsc` | Binance, Coinbase, Kraken, etc. |
| `market.crypto.reference_data-value` | `reference_data.avsc` | Binance, Coinbase, Kraken, etc. |

**Total Schemas**: 6 (3 data types × 2 asset classes)

### Rationale for Shared Schemas

**Why NOT per-exchange schemas?**

❌ Per-Exchange Approach:
- Would create 18+ schemas (3 data types × 6+ exchanges)
- Complex schema evolution (must update all exchange-specific schemas)
- Duplication of identical schemas
- Higher operational overhead

✅ Asset-Class-Level Approach:
- Only 6 schemas to manage
- Schema evolution applies to all exchanges simultaneously
- Trade/quote structure is uniform across exchanges within asset class
- Exchange name already in Avro record `exchange` field
- Topic name provides routing context

### Schema Compatibility

**Mode**: `BACKWARD` compatible

**Rules**:
- ✅ New fields must have defaults
- ✅ Can add optional fields
- ✅ Can remove optional fields
- ❌ Cannot remove required fields
- ❌ Cannot change field types

**Evolution Example**:
```avro
// Version 1
{
  "type": "record",
  "name": "Trade",
  "fields": [
    {"name": "symbol", "type": "string"},
    {"name": "price", "type": "double"},
    {"name": "quantity", "type": "long"}
  ]
}

// Version 2 (BACKWARD compatible)
{
  "type": "record",
  "name": "Trade",
  "fields": [
    {"name": "symbol", "type": "string"},
    {"name": "price", "type": "double"},
    {"name": "quantity", "type": "long"},
    {"name": "trade_condition", "type": ["null", "string"], "default": null}  // New optional field
  ]
}
```

### Asset Class Differences

**Equities-Specific Fields**:
- `isin` - International Securities Identification Number
- `company_id` - Unique company identifier
- `market_cap` - Market capitalization

**Crypto-Specific Fields**:
- `contract_type` - SPOT, FUTURES, PERPETUAL
- `settlement_currency` - USDT, BUSD, etc.
- `funding_rate` - For perpetual contracts

**Approach**: Use nullable fields with defaults for asset-class-specific fields.

---

## HFT/MFT Design Rationale

### Problem Statement

Market data platforms serving high-frequency trading firms must handle:
- **High message frequency**: 1000s-10000s messages/sec per exchange
- **Low latency requirements**: <100ms p99 ingestion → storage
- **Multiple exchanges**: Independent scaling needs
- **Future exchange additions**: Without system disruption
- **Operational isolation**: Exchange failures shouldn't cascade

### Alternative Approaches Considered

#### ❌ Approach 1: Single Flat Topics

```
market.trades (all exchanges mixed)
market.quotes (all exchanges mixed)
market.reference_data (all exchanges mixed)
```

**Problems**:
- Consumers must filter 95%+ of data
- No per-exchange scaling
- Hot partitions (popular symbols)
- Exchange failures affect all consumers
- Difficult to tune per-exchange settings

#### ❌ Approach 2: Exchange Prefix in Partition Key

```
market.trades (partition key: "ASX.BHP")
market.quotes (partition key: "ASX.BHP")
```

**Problems**:
- Still forces over-consumption
- No operational isolation
- Cannot tune retention/compression per exchange
- Complex routing logic in producers

#### ✅ Approach 3: Exchange-Level Topics (Selected)

```
market.{asset_class}.{data_type}.{exchange}
```

**Benefits**:
- Selective consumption (subscribe only to needed exchanges)
- Independent scaling per exchange
- Operational isolation
- Exchange-specific configurations
- Simple routing logic

### Performance Benefits

#### 1. Selective Consumption

**Old Approach (single topic)**:
```python
consumer.subscribe(['market.trades'])
for message in consumer:
    if message['exchange'] == 'ASX':  # Filter 95% of messages
        process(message)
```

**Network Transfer**: 100% (all exchanges)
**CPU Usage**: High (filtering overhead)
**Memory**: High (buffering unused messages)

**New Approach (exchange-level topics)**:
```python
consumer.subscribe(['market.equities.trades.asx'])
for message in consumer:
    process(message)  # No filtering needed
```

**Network Transfer**: 5-10% (only ASX)
**CPU Usage**: Low (no filtering)
**Memory**: Low (only needed messages)

**Result**: **10-100x reduction** in data transfer and processing overhead.

#### 2. Independent Scaling

**Example Configuration**:

```python
# High-volume exchange
NYSE_CONFIG = {
    'partitions': 100,
    'retention.ms': 3600000,  # 1 hour (high volume, short retention)
    'compression.type': 'lz4',
}

# Medium-volume exchange
ASX_CONFIG = {
    'partitions': 30,
    'retention.ms': 86400000,  # 24 hours
    'compression.type': 'lz4',
}

# Low-volume exchange
SMALL_EXCHANGE_CONFIG = {
    'partitions': 10,
    'retention.ms': 604800000,  # 7 days (low volume, longer retention OK)
    'compression.type': 'snappy',
}
```

**Benefit**: No over-provisioning. Each exchange gets appropriate resources.

#### 3. Failure Isolation

**Scenario**: ASX market data feed experiences issues (slow, high latency, partial outage).

**Old Approach**:
- Single `market.trades` topic gets backlogged
- All consumers affected (NYSE, NASDAQ, Binance)
- Global lag increases
- Difficult to isolate problem

**New Approach**:
- Only `market.equities.trades.asx` affected
- NYSE, NASDAQ, Binance consumers unaffected
- Clear metrics: ASX consumer lag spike
- Targeted remediation (restart ASX consumer only)

#### 4. Operational Clarity

**Monitoring Dashboard Example**:

```
Exchange: ASX
├── Topic: market.equities.trades.asx
│   ├── Message Rate: 500 msgs/sec
│   ├── Consumer Lag: 50 messages
│   └── Partition Balance: OK (15-20 msgs/partition)
├── Topic: market.equities.quotes.asx
│   ├── Message Rate: 2000 msgs/sec
│   ├── Consumer Lag: 150 messages
│   └── Partition Balance: OK (60-70 msgs/partition)
└── Status: Healthy

Exchange: Binance
├── Topic: market.crypto.trades.binance
│   ├── Message Rate: 3000 msgs/sec
│   ├── Consumer Lag: 5000 messages ⚠️
│   └── Partition Balance: Imbalanced (partition 5: 500 msgs) ⚠️
└── Status: Degraded - Action Required
```

**Benefit**: Clear per-exchange metrics. Easy to identify and debug issues.

---

## Subscription Patterns

The `SubscriptionBuilder` utility provides convenient methods for building topic subscriptions.

### Pattern 1: Single Exchange Consumer

**Use Case**: Trading strategy focused on one exchange (e.g., ASX-only equity strategy).

```python
from k2.kafka.patterns import get_subscription_builder

builder = get_subscription_builder()
topics = builder.subscribe_to_exchange('equities', 'asx')
# Result: ['market.equities.quotes.asx', 'market.equities.reference_data.asx', 'market.equities.trades.asx']

consumer.subscribe(topics)
```

**Data Volume**: Minimal (only ASX)
**Latency**: Lowest (no cross-exchange overhead)
**Best For**: Single-exchange strategies, market makers

### Pattern 2: Asset Class Consumer

**Use Case**: Cross-exchange analysis within asset class (e.g., crypto arbitrage bot).

```python
topics = builder.subscribe_to_asset_class('crypto')
# Result: All crypto topics (Binance, Coinbase, etc.)

consumer.subscribe(topics)
```

**Data Volume**: Medium (all exchanges for asset class)
**Latency**: Medium
**Best For**: Cross-exchange arbitrage, asset-class-wide analytics

### Pattern 3: Data Type Consumer

**Use Case**: Specialized consumers that only need one data type (e.g., trade execution monitor).

```python
from k2.kafka import DataType

topics = builder.subscribe_to_data_type(DataType.TRADES)
# Result: All trades topics (equities + crypto, all exchanges)

consumer.subscribe(topics)
```

**Data Volume**: Medium-High (all trades)
**Latency**: Medium
**Best For**: Trade execution monitoring, compliance systems

### Pattern 4: Filtered Subscription

**Use Case**: Specific exchanges and data types only.

```python
topics = builder.subscribe_to_asset_class(
    'equities',
    data_types=[DataType.TRADES, DataType.QUOTES],  # Exclude reference data
    exchanges=['asx', 'nyse']  # Only ASX and NYSE
)
# Result: ['market.equities.quotes.asx', 'market.equities.quotes.nyse',
#          'market.equities.trades.asx', 'market.equities.trades.nyse']

consumer.subscribe(topics)
```

**Data Volume**: Low-Medium (targeted)
**Latency**: Low
**Best For**: Multi-exchange strategies with specific data needs

### Pattern 5: Custom Topic List

**Use Case**: Very specific subscription requirements.

```python
topics = builder.subscribe_to_specific_topics([
    ('equities', DataType.TRADES, 'asx'),
    ('crypto', DataType.QUOTES, 'binance'),
])
# Result: ['market.crypto.quotes.binance', 'market.equities.trades.asx']

consumer.subscribe(topics)
```

**Data Volume**: Minimal (highly targeted)
**Latency**: Lowest
**Best For**: Specialized systems with exact requirements

---

## Configuration Management

### Topics Configuration File

**Location**: `config/kafka/topics.yaml`

This YAML file is the single source of truth for all topic configuration.

### Structure

```yaml
asset_classes:
  equities:
    description: "Traditional equity markets"
    exchanges:
      asx:
        name: "Australian Securities Exchange"
        partitions: 30
        country: "AU"
        timezone: "Australia/Sydney"

data_types:
  trades:
    description: "Trade execution events"
    partition_key: "symbol"
    schema_name: "trade"
    config:
      compression.type: "lz4"
      retention.ms: "604800000"

defaults:
  replication_factor: 1
  min_insync_replicas: 1

schema_registry:
  compatibility: "BACKWARD"
  subject_pattern: "market.{asset_class}.{data_type}-value"
```

### Adding a New Exchange

**No code changes required!**

1. Edit `config/kafka/topics.yaml`:

```yaml
asset_classes:
  equities:
    exchanges:
      nyse:  # New exchange
        name: "New York Stock Exchange"
        partitions: 100
        country: "US"
        timezone: "America/New_York"
        trading_hours: "09:30-16:00 EST"
        description: "Largest US equities exchange"
```

2. Run infrastructure initialization:

```bash
python scripts/init_infra.py
```

3. Done! Topics created:
   - `market.equities.trades.nyse`
   - `market.equities.quotes.nyse`
   - `market.equities.reference_data.nyse`

### Adding a New Asset Class

1. Update `config/kafka/topics.yaml`:

```yaml
asset_classes:
  commodities:  # New asset class
    description: "Commodity futures and options"
    exchanges:
      cme:
        name: "Chicago Mercantile Exchange"
        partitions: 40
        country: "US"
        timezone: "America/Chicago"
```

2. Create Avro schemas (if structure differs from existing):

```bash
# If commodities have different fields than equities/crypto
# Create new schemas: src/k2/schemas/commodity_trade.avsc, etc.
```

3. Run infrastructure initialization:

```bash
python scripts/init_infra.py
```

4. Register schemas:

```python
from k2.schemas import register_schemas
register_schemas()
```

---

## Migration from Legacy Topics

### Legacy Structure

**Old Topics**:
- `market.trades.raw` (6 partitions, hash by exchange.symbol)
- `market.quotes.raw` (6 partitions, hash by exchange.symbol)
- `market.reference_data` (1 partition)

### Migration Process

#### Pre-Migration Checklist

- [x] No producers writing to old topics (none implemented yet)
- [x] No consumers reading from old topics (none implemented yet)
- [ ] Config file created and validated
- [ ] Dry-run executed successfully
- [ ] Backup plan documented

#### Execution Steps

1. **Dry-run** (preview changes):

```bash
python scripts/migrate_topics.py --dry-run
```

Expected output:
```
DRY RUN: Would delete topics: ['market.reference_data', 'market.quotes.raw', 'market.trades.raw']
DRY RUN: Would create topics: ['market.crypto.quotes.binance', 'market.crypto.reference_data.binance', ...]
DRY RUN: Would register schemas: ['market.crypto.quotes-value', 'market.crypto.reference_data-value', ...]
```

2. **Execute migration**:

```bash
python scripts/migrate_topics.py
```

3. **Verify**:

```bash
# List topics
kafka-topics.sh --list --bootstrap-server localhost:9092

# Describe topic (check partition count)
kafka-topics.sh --describe --topic market.equities.trades.asx --bootstrap-server localhost:9092

# Check Schema Registry
curl http://localhost:8081/subjects
```

#### Post-Migration Validation

Run integration tests:

```bash
pytest tests/integration/test_topic_migration.py -v
```

Expected results:
- ✅ All 6 new topics exist
- ✅ Old topics deleted
- ✅ Partition counts correct (ASX: 30, Binance: 40, reference_data: 1)
- ✅ All 6 schemas registered
- ✅ No errors in Kafka or Schema Registry logs

### Rollback Plan

If migration fails:

1. Stop all services:
```bash
docker-compose down
```

2. Restore old topic definitions in `scripts/init_infra.py` (revert changes)

3. Restart services and recreate old topics:
```bash
docker-compose up -d
python scripts/init_infra.py
```

4. Re-register old schemas:
```python
from k2.schemas import register_schemas
register_schemas()
```

**Note**: Since no data exists yet (no producers), rollback is low-risk.

---

## Monitoring & Operations

### Key Metrics

**Per-Topic Metrics**:
- Message rate (messages/sec)
- Byte rate (MB/sec)
- Consumer lag (messages behind)
- Producer throughput
- Error rate

**Per-Partition Metrics**:
- Partition size (MB)
- Leader election count
- Replica lag
- Messages per partition (balance check)

**Schema Registry Metrics**:
- Schema registration rate
- Schema compatibility errors
- Subject count

### Recommended Alerts

#### Critical Alerts (Page On-Call)

- **Consumer lag > 5000 messages for >5 minutes**
  - Indicates consumer cannot keep up
  - Action: Scale consumers or investigate slow processing

- **Partition imbalance >50%**
  - Example: Partition 5 has 500 messages, others have 50
  - Indicates hot partition (popular symbol)
  - Action: Review partition key distribution

- **Topic creation failure**
  - New exchange topics failed to create
  - Action: Check Kafka broker health, disk space

- **Schema registration failure**
  - Incompatible schema change attempted
  - Action: Review schema evolution, fix compatibility issue

#### Warning Alerts (Monitor, Don't Page)

- **Consumer lag > 1000 messages**
  - Early warning of degradation
  - Action: Monitor, prepare to scale if increases

- **Message rate increase >3x baseline**
  - Unexpected traffic spike
  - Action: Verify data source, check for duplicates

- **Broker disk usage >70%**
  - Approaching capacity
  - Action: Plan retention reduction or storage expansion

### Monitoring Dashboard Layout

**Top Level** (Overview):
```
┌─────────────────────────────────────────────────────────┐
│ K2 Kafka Topics - System Overview                      │
├─────────────────────────────────────────────────────────┤
│ Total Topics: 6                                         │
│ Total Messages/sec: 5,500                               │
│ Total Consumer Lag: 200 messages                        │
│ Status: Healthy ✅                                      │
└─────────────────────────────────────────────────────────┘
```

**Per-Exchange** (Drill-down):
```
┌─────────────────────────────────────────────────────────┐
│ Exchange: ASX (Australian Securities Exchange)          │
├─────────────────────────────────────────────────────────┤
│ Trades Topic: market.equities.trades.asx                │
│   • Message Rate: 500 msgs/sec                          │
│   • Consumer Lag: 50 messages                           │
│   • Partitions: 30 (balanced)                           │
│                                                          │
│ Quotes Topic: market.equities.quotes.asx                │
│   • Message Rate: 2,000 msgs/sec                        │
│   • Consumer Lag: 150 messages                          │
│   • Partitions: 30 (balanced)                           │
│                                                          │
│ Reference Data Topic: market.equities.reference_data.asx│
│   • Message Rate: 1 msg/min                             │
│   • Consumer Lag: 0 messages                            │
│   • Partitions: 1 (compacted)                           │
└─────────────────────────────────────────────────────────┘
```

---

## Producer/Consumer Best Practices

### Producer Configuration

#### For QUOTES (prioritize speed over durability)

```python
from confluent_kafka import SerializingProducer

QUOTES_PRODUCER_CONFIG = {
    'bootstrap.servers': 'localhost:9092',
    'acks': 1,  # Leader ack only (faster)
    'compression.type': 'lz4',  # Fastest compression
    'linger.ms': 0,  # Send immediately
    'batch.size': 16384,
    'buffer.memory': 33554432,
    'max.in.flight.requests.per.connection': 5,
}

producer = SerializingProducer(QUOTES_PRODUCER_CONFIG)
```

#### For TRADES (prioritize reliability)

```python
TRADES_PRODUCER_CONFIG = {
    'bootstrap.servers': 'localhost:9092',
    'acks': 'all',  # All replicas ack (safer)
    'compression.type': 'lz4',
    'linger.ms': 1,  # Micro-batching OK
    'enable.idempotence': True,  # Prevent duplicates
    'max.in.flight.requests.per.connection': 1,
}

producer = SerializingProducer(TRADES_PRODUCER_CONFIG)
```

#### Partition Key Best Practice

```python
from k2.kafka import get_topic_builder, DataType

topic_builder = get_topic_builder()
topic = topic_builder.build_topic_name('equities', DataType.TRADES, 'asx')

# Produce with symbol as partition key
producer.produce(
    topic=topic,
    key="BHP",  # Symbol as partition key
    value=trade_record,  # Avro-serialized record
    on_delivery=delivery_callback  # Track success/failure
)
```

### Consumer Configuration

#### For Low-Latency Consumption

```python
from confluent_kafka import DeserializingConsumer

CONSUMER_CONFIG = {
    'bootstrap.servers': 'localhost:9092',
    'group.id': 'k2-iceberg-writer',
    'fetch.min.bytes': 1,  # Don't wait for batch
    'fetch.max.wait.ms': 100,  # Low latency
    'max.partition.fetch.bytes': 1048576,  # 1MB
    'enable.auto.commit': False,  # Manual commit for exactly-once
    'isolation.level': 'read_committed',  # Only read committed transactions
}

consumer = DeserializingConsumer(CONSUMER_CONFIG)
```

#### Subscription Example

```python
from k2.kafka.patterns import get_subscription_builder

builder = get_subscription_builder()
topics = builder.subscribe_to_exchange('equities', 'asx')

consumer.subscribe(topics)

# Consume and process
while True:
    msg = consumer.poll(timeout=1.0)
    if msg is None:
        continue

    # Process message
    process(msg.value())

    # Manual commit after successful processing
    consumer.commit(message=msg)
```

---

## Future Enhancements

### Multi-Region Deployment

For global deployment across regions:

**Architecture**:
```
US-EAST Region                     EU-WEST Region
├── Kafka Cluster                  ├── Kafka Cluster
│   ├── market.equities.trades.nyse│   ├── market.equities.trades.lse
│   └── market.crypto.trades.*    │   └── market.equities.trades.euronext
└── MirrorMaker 2.0                └── MirrorMaker 2.0
        ↓                                  ↓
    ┌──────────────────────────────────────┐
    │   APAC Region                        │
    │   ├── Kafka Cluster (aggregated)    │
    │   └── All topics mirrored           │
    └──────────────────────────────────────┘
```

**Benefits**:
- Regional producers (low latency local writes)
- Global consumers (read aggregated data)
- Fault tolerance (region failover)

### Stream Processing Topologies

**Per-Exchange Processing**:

```python
# Kafka Streams or Flink topology
builder = StreamsBuilder()

# Process ASX trades independently
asx_trades = builder.stream('market.equities.trades.asx')
asx_trades \
    .filter(lambda trade: trade['price'] > 0) \
    .mapValues(lambda trade: enrich_trade(trade)) \
    .to('market.equities.trades.asx.enriched')

# Process Binance trades independently
binance_trades = builder.stream('market.crypto.trades.binance')
binance_trades \
    .filter(lambda trade: trade['price'] > 0) \
    .mapValues(lambda trade: enrich_trade(trade)) \
    .to('market.crypto.trades.binance.enriched')
```

**Cross-Exchange Analytics**:

```python
# Join trades from multiple exchanges
equity_trades = builder.stream(['market.equities.trades.asx', 'market.equities.trades.nyse'])
equity_trades \
    .groupBy(lambda trade: trade['symbol']) \
    .windowedBy(TimeWindows.of(Duration.ofSeconds(60))) \
    .aggregate(calculate_vwap) \
    .to('analytics.vwap.equities')
```

### Advanced Partitioning

**Symbol Popularity-Based Partitioning**:

For extremely popular symbols (e.g., AAPL, BTC), consider:
- Dedicated partitions for top 10 symbols
- Hash-based partitioning for remaining symbols
- Prevents hot partitions

**Implementation**:
```python
def get_partition_key(symbol, num_partitions):
    if symbol in TOP_10_SYMBOLS:
        return DEDICATED_PARTITION_MAP[symbol]
    else:
        return hash(symbol) % num_partitions
```

---

## Appendix

### Glossary

| Term | Definition |
|------|------------|
| **Asset Class** | Category of financial instruments (equities, crypto, commodities) |
| **Partition Key** | Field used to determine message partition assignment |
| **Schema Subject** | Schema Registry identifier for a schema version |
| **Compaction** | Log compaction keeps only latest value per key |
| **Consumer Lag** | Number of messages behind real-time a consumer is |
| **HFT** | High-Frequency Trading - trading strategies with sub-second execution |
| **MFT** | Medium-Frequency Trading - trading strategies with second-to-minute execution |

### References

- [Kafka Documentation - Topics](https://kafka.apache.org/documentation/#topicconfigs)
- [Confluent Schema Registry](https://docs.confluent.io/platform/current/schema-registry/index.html)
- [Avro Specification](https://avro.apache.org/docs/current/spec.html)
- [K2 Platform README](../../README.md)

### Change Log

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-10 | 1.0 | Initial documentation for exchange-level topic architecture |

---

**Document Owner**: K2 Platform Team
**Review Frequency**: Quarterly
**Next Review**: 2026-04-10
