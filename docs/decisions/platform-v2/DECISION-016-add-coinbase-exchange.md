# DECISION-016: Add Coinbase Advanced Trade as 3rd Exchange

**Date**: 2026-02-18
**Status**: Accepted
**Author**: Principal Data Engineer

---

## Context

The platform currently ingests spot trade data from two exchanges:
- **Binance** — 12 USDT-margined pairs (highest global liquidity)
- **Kraken** — 11 USD pairs (EUR/USD-margined, EU-regulated)

Both provide global/non-US perspective. Adding a US-regulated exchange creates exchange-diversity risk coverage and provides an independent USD price reference.

---

## Decision

**Add Coinbase Advanced Trade as the 3rd exchange.**

- **API**: Coinbase Advanced Trade WebSocket (`wss://advanced-trade-ws.coinbase.com`)
- **Channel**: `market_trades` (no authentication required for public data)
- **Pairs**: 11 USD spot pairs (same assets as Kraken, standard tickers)
- **Resource delta**: +0.5 CPU / +512MB RAM for the `feed-handler-coinbase` container

---

## Rationale

| Factor | Reasoning |
|--------|-----------|
| **US jurisdiction** | Coinbase is a CFTC/SEC-regulated US exchange; complements Binance (unregulated) and Kraken (EU-regulated) |
| **USD pairs** | Complements Kraken's USD view with an independent US market price source |
| **Clean API** | Object-based WS protocol; simpler than Kraken's array format |
| **No auth needed** | Public `market_trades` channel requires no API key |
| **Consistent tickers** | Uses standard ISO tickers (DOGE, not XDG like Kraken) |

---

## Alternatives Considered

| Alternative | Reason Rejected |
|-------------|----------------|
| **OKX** | Primarily USDT pairs — duplicates Binance view without jurisdiction diversity |
| **Bybit** | Derivative-focused; spot data is secondary product |
| **FTX** | Defunct |
| **Gemini** | Lower volume; smaller instrument universe vs Coinbase |

---

## Implementation

### New Files
| File | Purpose |
|------|---------|
| `services/feed-handler-kotlin/src/.../CoinbaseWebSocketClient.kt` | WebSocket client (mirrors KrakenWebSocketClient pattern) |
| `docker/clickhouse/schema/11-bronze-coinbase.sql` | Kafka Engine queue + bronze table + MV |
| `docs/decisions/platform-v2/DECISION-016-add-coinbase-exchange.md` | This file |

### Modified Files
| File | Change |
|------|--------|
| `services/.../TradeNormalizer.kt` | Added `normalizeCoinbase()` |
| `services/.../Main.kt` | Added `"coinbase"` branch to exchange dispatcher |
| `services/.../application.conf` | Added `coinbase {}` WebSocket URL config block |
| `config/instruments.yaml` | Added `coinbase:` section with 11 pairs |
| `docker-compose.v2.yml` | Added `feed-handler-coinbase` service + 2 Redpanda topics in `redpanda-init` |
| `docker/iceberg/ddl/02-bronze-tables.sql` | Added `cold.bronze_trades_coinbase` Iceberg table |
| `docker/offload/flows/iceberg_offload_flow.py` | Added `bronze_trades_coinbase` to `TABLE_CONFIG["bronze"]` |
| `docker/postgres/ddl/offload-watermarks.sql` | Added `bronze_trades_coinbase` watermark seed |

### Key Normalisation Decisions
- `product_id: "BTC-USD"` → `symbol="BTCUSD"`, `canonical_symbol="BTC/USD"` (replace `-`)
- `side: "BUY"/"SELL"` → already taker perspective; maps directly to `TradeSide` enum
- `time` (ISO8601 nanoseconds) → `Instant.parse(time).toEpochMilli()` (no new dependency)
- `trade_id` → provided by Coinbase (no generation needed, unlike Kraken)
- `sequence_num` → message-level (envelope), stored in `TradeMetadata.sequenceNumber`

---

## Resource Impact

```
Before: 15.0 CPU / 21.25 GB RAM (12 services)
After:  15.5 CPU / 21.75 GB RAM (13 services)
Delta:  +0.5 CPU / +0.5 GB RAM
Budget: 0.5 CPU / 18.25 GB remaining (target: 16 CPU / 40 GB)
```

---

## Instruments

11 USD spot pairs (no BNB — not listed on Coinbase; DOGE instead of XDG for Kraken):

```
BTC-USD, ETH-USD, SOL-USD, XRP-USD, ADA-USD, DOT-USD,
LINK-USD, DOGE-USD, LTC-USD, AVAX-USD, ATOM-USD
```

---

## Verification Checklist

- [ ] `./gradlew test` passes (new `TradeNormalizerTest` + `InstrumentsLoaderTest`)
- [ ] `./gradlew build` compiles without errors
- [ ] `docker compose -f docker-compose.v2.yml up -d` starts all 13 services
- [ ] `rpk topic list | grep coinbase` shows 2 topics
- [ ] `docker logs feed-handler-coinbase` shows "Subscription confirmed"
- [ ] `rpk topic consume market.crypto.trades.coinbase.raw --num 5` shows trade JSON
- [ ] `SELECT count() FROM k2.bronze_trades_coinbase` returns growing count
- [ ] `rpk topic consume market.crypto.trades.coinbase --num 5` shows normalized Avro
- [ ] Prefect offload run populates `cold.bronze_trades_coinbase` in Iceberg
- [ ] `offload_watermarks` table has row for `bronze_trades_coinbase`
