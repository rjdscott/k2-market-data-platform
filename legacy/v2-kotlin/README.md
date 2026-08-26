# K2 v2 Kotlin feed handlers (archived)

This directory holds the v2 capture tier: three Kotlin/Ktor containers, one per
exchange, that were K2's only ingestion path from February 2026 until the v3
Rust capture tier replaced them. It is **archived** — kept for reference, for the
migration story in [`docs/MIGRATION-JOURNEY.md`](../../docs/MIGRATION-JOURNEY.md),
and because it is the only implementation the parity comparison was measured
against. It is not maintained, not built by CI, and not part of the running stack.

## What it was

One `feed-handler` image ([`Dockerfile`](./Dockerfile)), run three times with
`K2_EXCHANGE` set to `binance`, `kraken` or `coinbase`. Each container opened one
public WebSocket, published the exchange's own JSON verbatim to a raw topic, and
published a normalised Avro record to a second topic. Kotlin 2.3 / Ktor 3.1 on
JDK 21, Micrometer on `:8082/metrics`, no Spring. The instrument list came from
[`config/instruments.yaml`](../../config/instruments.yaml) via
`InstrumentsLoader.kt` (native symbols only — the `canonical` column was v3's).

Measured under live load: ~150 msg/s sustained across all three, ~0.03 CPU and
134 MiB per container
([`docs/benchmarks/2026-02-19-v2-baseline.md`](../../docs/benchmarks/2026-02-19-v2-baseline.md)).
They were never the bottleneck. What retired them is what they could not be asked
to do.

## The v2 topics it wrote

Six topics, created by [`docker/redpanda/init.sh`](../../docker/redpanda/init.sh)
step 1. They are **frozen, not deleted** — they keep their data and their
registered subjects until the Phase E ClickHouse cutover drops the `k2` database
and the `.raw` topics together.

| Topic | Partitions | Payload | Key | Read by |
|---|---|---|---|---|
| `market.crypto.trades.binance.raw` | 40 | exchange JSON, verbatim | symbol | ClickHouse Kafka engine → `k2.bronze_*` |
| `market.crypto.trades.kraken.raw` | 20 | exchange JSON, verbatim | exchange name | ClickHouse Kafka engine → `k2.bronze_*` |
| `market.crypto.trades.coinbase.raw` | 20 | exchange JSON, verbatim | exchange name | ClickHouse Kafka engine → `k2.bronze_*` |
| `market.crypto.trades.binance` | 40 | Avro `NormalizedTrade` | symbol | nothing |
| `market.crypto.trades.kraken` | 20 | Avro `NormalizedTrade` | exchange name | nothing |
| `market.crypto.trades.coinbase` | 20 | Avro `NormalizedTrade` | exchange name | nothing |

The three normalized Avro topics had no consumer for their whole life: the
medallion pipeline reads the `.raw` JSON topics directly
([ADR-009](../../docs/adr/ADR-009-medallion-in-clickhouse.md)). Their contract,
[`schemas/avro/normalized-trade.avsc`](../../schemas/avro/normalized-trade.avsc),
stays in the repo — `KafkaProducerService.kt:115` loads it at producer start, so
the archive needs it to run, and the three `-value` subjects are still registered.

## Why it was retired

[ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md) has the argument and
[ADR-002](../../docs/adr/ADR-002-kotlin-feed-handlers.md) is the decision it
supersedes. The short version: every gap was on the frame-receipt path, so
closing them was the rewrite either way.

- The only wall clock on the trade path was taken *after* JSON parse and
  normalisation (`TradeNormalizer.kt:28`), so exchange-clock skew and platform
  delay are inseparable in every row it ever wrote.
- No L2 book at all, and no place to put one on the same connection.
- Coinbase's `sequence_num` was parsed and discarded (`CoinbaseWebSocketClient.kt:178`)
  — a dropped message was silent.
- Kraken ran WS v1 with synthesised trade IDs, `"KRAKEN-${ms}-${pair.hashCode()}"`
  (`TradeNormalizer.kt:60`), which collide by construction.
- No replay determinism: `BigDecimal` on the record path, `HashMap` iteration,
  wall-clock reads wherever they were convenient.

Retirement was gated, not scheduled: per-symbol trade counts and IDs from the
Rust tier had to match these handlers exactly over a labelled parallel-run window
before the containers came out of `docker-compose.yml`. ADR-019's Outcome section
records that window.

## Layout

| Path | Contents |
|---|---|
| `src/main/kotlin/com/k2/feedhandler/` | `Main.kt`, one `*WebSocketClient.kt` per exchange, `TradeNormalizer.kt`, `KafkaProducerService.kt`, `InstrumentsLoader.kt`, `MetricsServer.kt`, `Models.kt` |
| `src/main/resources/` | `application.conf` (HOCON, env-substituted), `logback.xml` |
| `src/test/kotlin/` | 20 tests: `TradeNormalizerTest` (7), `InstrumentsLoaderTest` (13) |
| `runbooks/` | The v2 feed-handler incident procedures, archived with the code |
| `Dockerfile` | Multi-stage Gradle → `eclipse-temurin:21-jre-alpine`; build context is the **repository root** |

## Running it from the archive

Nothing in the current stack builds or starts these. Both commands below still
work from a clean checkout.

```bash
# Unit tests (JDK 21 build image; no local JDK needed)
make test-legacy-kotlin

# Equivalently, by hand:
docker run --rm -v "$PWD":/project -w /project/legacy/v2-kotlin \
  -e GRADLE_USER_HOME=/tmp/.gradle gradle:8.12-jdk21 ./gradlew test --no-daemon

# Build the image (context is the repo root — the Dockerfile COPYs `schemas/`)
docker build -f legacy/v2-kotlin/Dockerfile -t k2-feed-handler:v2-archived .
```

Running one against the live stack would resume writing to the frozen v2 topics,
which is not what "frozen" means — do it against a throwaway broker, not this one:

```bash
docker run --rm --network k2-net \
  -e K2_EXCHANGE=kraken \
  -e K2_INSTRUMENTS_FILE=/app/config/instruments.yaml \
  -e K2_KAFKA_BOOTSTRAP_SERVERS=<your-throwaway-broker>:9092 \
  -e K2_KAFKA_SCHEMA_REGISTRY_URL=http://<your-throwaway-broker>:8081 \
  -v "$PWD/config/instruments.yaml":/app/config/instruments.yaml:ro \
  k2-feed-handler:v2-archived
```

> **`config/instruments.yaml` moved on after this code did.** Kraken's natives
> there are now the WS **v2** spellings (`BTC/USD`, `DOGE/USD`). These handlers
> speak Kraken WS **v1**, which wants `XBT/USD` and `XDG/USD`, so a Kraken run
> from the archive needs a local registry file with the v1 spellings. Binance and
> Coinbase are unaffected.

## Known issues (left as-is)

- `KafkaProducerService.produceRawJson` keys Kraken and Coinbase raw records by
  the *exchange name*, so both topics hashed every record onto one partition of
  20. Only Binance keyed by symbol.
- `normalized-trade.avsc` puts `logicalType` beside `type`, where Avro ignores it,
  and carries prices as strings. Nothing consumed the topic, so nothing caught it
  ([ADR-020](../../docs/adr/ADR-020-avro-fixed-point-contracts.md)).
- `Models.kt` documents itself as matching the Avro schema; the two drifted and
  the schema was never the source of truth for the Kotlin type.
- No `.gitignore` of its own — `build/` and `.gradle/` are covered by the repo
  root's.
