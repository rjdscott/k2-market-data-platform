# K2 v2 Kotlin feed handlers (archived)

The v2 capture tier: three Kotlin/Ktor containers, one per exchange. It is kept for reference, for the migration story in [`docs/MIGRATION-JOURNEY.md`](../../docs/MIGRATION-JOURNEY.md), and because it is the only implementation the parity comparison was measured against. It is not maintained, not built by CI, and not part of the running stack. See [`../README.md`](../README.md) for the archive rules.

## What it was

One `feed-handler` image ([`Dockerfile`](./Dockerfile)), run three times with
`K2_EXCHANGE` set to `binance`, `kraken` or `coinbase`. Each container opened one
public WebSocket, published the exchange's own JSON verbatim to a raw topic, and
published a normalised Avro record to a second topic. Kotlin 2.3 / Ktor 3.1 on
JDK 21, Micrometer on `:8082/metrics`, no Spring. The instrument list came from
[`config/instruments.yaml`](../../config/instruments.yaml) via
`InstrumentsLoader.kt` (native symbols only; the `canonical` column was v3's).

It was K2's only ingestion path from February 2026 ([ADR-002](../../docs/adr/ADR-002-kotlin-feed-handlers.md) Outcome is dated 2026-02) until the v3 Rust capture tier replaced it (retirement dates under "State at archive").

Measured under live load, quoted as the benchmark states it: ~150 msg/s across the
three exchanges, and **one** resource data point: `feed-handler-binance`, *"the
busiest of the three"*, at **~0.03 CPU / 134 MiB** against its 0.5 CPU / 512 MB limit
([`docs/benchmarks/2026-02-19-v2-baseline.md`](../../docs/benchmarks/2026-02-19-v2-baseline.md)).
Kraken and Coinbase were never measured separately, so that is not a per-container
figure. They were never the bottleneck. What retired them is what they could not be asked
to do.

### The v2 topics it wrote

Six topics, created by `docker/redpanda/init.sh` before Phase E. **They no longer exist:**
the Phase E cutover deleted all six and `redpanda-init` stopped creating them
(`9011d94`'s message: "the six v2 topics deleted"). The table records what they were.

| Topic | Partitions | Payload | Key | Read by |
|---|---|---|---|---|
| `market.crypto.trades.binance.raw` | 40 | exchange JSON, verbatim | symbol | ClickHouse Kafka engine, `k2.bronze_*` |
| `market.crypto.trades.kraken.raw` | 20 | exchange JSON, verbatim | exchange name | ClickHouse Kafka engine, `k2.bronze_*` |
| `market.crypto.trades.coinbase.raw` | 20 | exchange JSON, verbatim | exchange name | ClickHouse Kafka engine, `k2.bronze_*` |
| `market.crypto.trades.binance` | 40 | Avro `NormalizedTrade` | symbol | nothing |
| `market.crypto.trades.kraken` | 20 | Avro `NormalizedTrade` | exchange name | nothing |
| `market.crypto.trades.coinbase` | 20 | Avro `NormalizedTrade` | exchange name | nothing |

The three normalized Avro topics had no consumer for their whole life: the
medallion pipeline read the `.raw` JSON topics directly
([ADR-009](../../docs/adr/ADR-009-medallion-in-clickhouse.md)). Their contract,
[`schemas/avro/normalized-trade.avsc`](../../schemas/avro/normalized-trade.avsc),
stays in the repo: `KafkaProducerService.kt:115` loads it at producer start, so
the archive needs it to run. Whether the three `-value` registry subjects were deleted with the topics is not recorded.

| Path | Contents |
|---|---|
| `src/main/kotlin/com/k2/feedhandler/` | `Main.kt`, one `*WebSocketClient.kt` per exchange, `TradeNormalizer.kt`, `KafkaProducerService.kt`, `InstrumentsLoader.kt`, `MetricsServer.kt`, `Models.kt` |
| `src/main/resources/` | `application.conf` (HOCON, env-substituted), `logback.xml` |
| `src/test/kotlin/` | 20 tests: `TradeNormalizerTest` (7), `InstrumentsLoaderTest` (13) |
| `runbooks/` | `feed-handler-crash.md`, plus the alert rules `feed-handler-alerts.yml` and `clickhouse-v2-ingest-alerts.yml` that moved with the tier |
| `Dockerfile` | Multi-stage Gradle to `eclipse-temurin:21-jre-alpine`; build context is the **repository root** |

## Why it was archived

[ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md) has the argument and
[ADR-002](../../docs/adr/ADR-002-kotlin-feed-handlers.md) is the decision it
supersedes. The short version: every gap was on the frame-receipt path, so
closing them was the rewrite either way.

- The only wall clock on the trade path was taken *after* JSON parse and
  normalisation (`TradeNormalizer.kt:28`), so exchange-clock skew and platform
  delay are inseparable in every row it ever wrote.
- No L2 book at all, and no place to put one on the same connection.
- Coinbase's `sequence_num` was parsed and discarded (`CoinbaseWebSocketClient.kt:178`),
  so a dropped message was silent.
- Kraken ran WS v1 with synthesised trade IDs, `"KRAKEN-${ms}-${pair.hashCode()}"`
  (`TradeNormalizer.kt:60`), which collide by construction.
- No replay determinism: `BigDecimal` on the record path, `HashMap` iteration,
  wall-clock reads wherever they were convenient.

Retirement was gated, not scheduled: per-symbol trade counts and IDs from the
Rust tier had to match these handlers over a labelled 2-hour parallel-run window
(2026-08-26 14:15Z to 16:15Z, per ADR-019's Outcome) before the containers came out
of `docker-compose.yml`. ADR-019's Outcome ("Retirement") records that window; its Binance result is 12 of 12 symbols PASS. Retiring the three JVMs took the stack from
16.10 CPU / 23.125 GiB across 18 long-running services to 14.60 CPU / 21.625 GiB
across 15, per the same Outcome.

## What replaced each piece

| Archived | Successor |
|---|---|
| three `feed-handler-*` containers (`src/main/kotlin/`) | [`services/capture-rust/`](../../services/capture-rust/) ([ADR-019](../../docs/adr/ADR-019-rust-capture-tier.md)) |
| `TradeNormalizer.kt`, normalised Avro to `market.crypto.trades.<ex>` | Rust normalisation and the v3 contracts: [`schemas/avro/trade.avsc`](../../schemas/avro/trade.avsc), [`raw-message.avsc`](../../schemas/avro/raw-message.avsc) ([ADR-020](../../docs/adr/ADR-020-avro-fixed-point-contracts.md)) |
| raw JSON topics `market.crypto.trades.<ex>.raw` | `raw.*` v3 topics, landed verbatim in the lake's `raw.messages`; topics in [`docker/redpanda/init.sh`](../../docker/redpanda/init.sh) |
| no L2 book | `book` product and [`book-snapshot-l2.avsc`](../../schemas/avro/book-snapshot-l2.avsc) ([ADR-027](../../docs/adr/ADR-027-book-snapshot-and-sequencing.md)) |
| `runbooks/feed-handler-crash.md` | [`capture-down.md`](../../docs/runbooks/capture-down.md), [`capture-feed-stale.md`](../../docs/runbooks/capture-feed-stale.md) |
| `FeedHandler*` alert rules | `capture-alerts.yml` rules in `docker/prometheus/rules/` (10 v3 capture rules per ADR-019 Outcome) |
| `InstrumentsLoader.kt` reading `config/instruments.yaml` | the same registry file, read by the Rust crate |

## State at archive

Moved here from `services/feed-handler-kotlin/` in `2b20804` ("chore(v3): retire the Kotlin feed handlers", #72), authored 2026-08-27 and first contained in tag `v3-phase-d`. ADR-019's Outcome heading dates the retirement 2026-08-26, one day earlier than the commit; both dates are as recorded. `git diff --stat 2b20804 HEAD -- legacy/v2-kotlin` is empty, so the archived copy is unmodified.

## Running or verifying it today

Nothing in the current stack builds or starts these. The definitions below exist; I read them, I did not run them.

```bash
# Unit tests (JDK 21 build image; no local JDK needed). Defined in the Makefile
# as `docker run --user <uid>:<gid> ... -w /project/legacy/v2-kotlin gradle:8.12-jdk21 ./gradlew test --no-daemon`.
make test-legacy-kotlin

# Build the image (context is the repo root; the Dockerfile COPYs `schemas/`)
docker build -f legacy/v2-kotlin/Dockerfile -t k2-feed-handler:v2-archived .
```

`make test-legacy-kotlin` is outside `make test` and outside CI (see the comment above it in the `Makefile`). It exercises the 20 unit tests, which cover normalisation and instrument-registry loading. It does not exercise a WebSocket or Kafka connection, and nothing in the stack feeds the handlers any more.

Running one against the live stack would resume writing topics that Phase E deleted, and the stack no longer has a consumer for them. Do it against a throwaway broker, not this one. No
`--network`, deliberately: joining the live stack's network is the mistake this
paragraph warns about, and that network is not called `k2-net` anyway, because compose prefixes
it, `k2-market-data-platform_k2-net`.

```bash
docker run --rm \
  -e K2_EXCHANGE=kraken \
  -e K2_INSTRUMENTS_FILE=/app/config/instruments.yaml \
  -e K2_KAFKA_BOOTSTRAP_SERVERS=<your-throwaway-broker>:9092 \
  -e K2_KAFKA_SCHEMA_REGISTRY_URL=http://<your-throwaway-broker>:8081 \
  -v "$PWD/legacy/v2-kotlin/src/test/resources/instruments-v1.yaml":/app/config/instruments.yaml:ro \
  k2-feed-handler:v2-archived
```

> **`config/instruments.yaml` moved on after this code did.** Kraken's natives
> there are now the WS **v2** spellings (`BTC/USD`, `DOGE/USD`). These handlers
> speak Kraken WS **v1**, which wants `XBT/USD` and `XDG/USD`, which is why the
> command above mounts
> [`src/test/resources/instruments-v1.yaml`](src/test/resources/instruments-v1.yaml),
> the registry frozen at the retirement commit and the same file the archive's
> own tests assert against. Binance and Coinbase are unaffected.

## Known residue

- `KafkaProducerService.produceRawJson` keys Kraken and Coinbase raw records by
  the *exchange name*, so both topics hashed every record onto one partition of
  20. Only Binance keyed by symbol.
- `normalized-trade.avsc` puts `logicalType` beside `type`, where Avro ignores it,
  and carries prices as strings. Nothing consumed the topic, so nothing caught it
  ([ADR-020](../../docs/adr/ADR-020-avro-fixed-point-contracts.md)).
- `Models.kt` documents itself as matching the Avro schema; the two drifted and
  the schema was never the source of truth for the Kotlin type.
- No `.gitignore` of its own: `build/` and `.gradle/` are covered by the repo
  root's.
- **Dependencies are frozen.** `.github/dependabot.yml` says the only Gradle project is this one and has no gradle ecosystem, so nothing updates it, and CI's Trivy scan skips `legacy/`. No advisory scan of its dependencies is recorded.
- Live-tree mentions: `Makefile` (`test-legacy-kotlin`), `docker-compose.yml`, `ruff.toml`, `schemas/README.md`, `tests/test_contracts.py`, `scripts/parity/compare_trades.py`, `config/instruments.yaml`, `CLAUDE.md`, `CONTRIBUTING.md`. `scripts/check-docs.sh` scans this README (link and word gates).
