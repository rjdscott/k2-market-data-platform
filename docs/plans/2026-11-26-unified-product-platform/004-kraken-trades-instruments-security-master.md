# Phase 004: Kraken trades, instruments and the security master

**Depends on:** `003` (tag `v3.0.0-alpha.5`); the owner's answers on landing retention and on
where `LAKEKEEPER_ENCRYPTION_KEY` is kept off the host; the nightly backup timer installed by
the owner (`make backup-install`) before the restore drill.
**Decision it implements:** [`ADR-056-unified-data-product-platform.md`](../../adr/ADR-056-unified-data-product-platform.md). New ADRs, each
in the pull request that builds it: the Kafka connector with a landing per product, which gives
up the shared archive of [ADR-021](../../adr/ADR-021-raw-first-archive-and-lineage.md)
(Proposed) while keeping exactly-once resume from the snapshot
([ADR-022](../../adr/ADR-022-exactly-once-via-snapshot-offsets.md)) and the rule that the
landing commits first and never blocks
([ADR-047](../../adr/ADR-047-ingest-batch-audit-after-publish.md)); landing retention, if the
owner chooses a rolling window. The security master follows the type-2 conventions of
[ADR-030](../../adr/ADR-030-scd2-security-master.md).
**Delivers:** live crypto data again, as products: the Kafka connector and the public-API
connector; `kraken-trades`, `kraken-instruments` and `security-master`; `canonical-trades` and
`candles` at every timeframe on Kraken; per-product maintenance; the operator hold;
`make product-verify`, `make rebuild`, `make restore`; the parity pins re-made on the new
tables; the first restore drill.
**Exit:** a killed Kafka connector resumes exactly once; `make product-verify` accounts for
every offset; an unknown symbol is held without stopping the venue; a failed venue test leaves
the derived products unchanged; a paused input makes the derived product `partial`; the
delayed role sees nothing newer than 20 minutes and a null connection id; after 48 hours of
five-minute runs the delete-file count stays under its bound and the chain's run times are
published; the restore drill passes and records its time; tag `v3.0.0-alpha.6`.

## Scope

**Connectors**

- **Kafka**, in Spark, reusing `docker/lake/offsets.py` and the framing half of
  `docker/lake/wire.py`, split here from the trade constants. A product reads its own topic at
  its own position. The envelope contract is the capture's `RawMessage`
  (`schemas/avro/raw-message.avsc`), declared by `market-capture`; the filter keeps
  `stream = trade` and counts the rest, so landed plus skipped equals the offset span. The
  landing table `lake.<product>.landing` holds the kept records byte for byte, with end offsets
  in the same commit. An active product whose offset is below the log start fails loudly;
  moving on takes an operator's per-product accept-data-loss run (an Airflow trigger with
  `conf`), recorded as an `offset_gap` on the product page. A paused product lands nothing and
  resumes at the log start.
- **Public API**, in the fetch container: `url`, `allowHosts`, pagination (`none`, `next-link`,
  `page`), `maxPages`, `minIntervalS`, `records`, and `volatileFields`, so a response unchanged
  apart from those fields lands nothing. Every fetch is logged; each changed response lands as
  one object named by its sha256.

**Products** (owner decision: each venue has a trades product and an instruments product)

- `market-capture/kraken-trades`: landing, `bronze_trade` (the frame as sent, keyed by partition
  and offset), `silver_trade` (one typed row per trade, every delivery kept and flagged, built by
  dbt with `UNNEST ... WITH ORDINALITY` in place of Spark's `posexplode`), and the held table
  `silver_trade_unresolved`, owner-only. A trade whose symbol resolves to nothing is held; the
  rest publish; a generated audit "unresolved rows = 0" alerts, marks the page `degraded` and
  starts an instruments run; held rows move into silver once they resolve.
- `market-capture/kraken-instruments`: Kraken's public `AssetPairs` endpoint, hourly and on
  demand; `silver_listing`, one row per pair and version.
- `reference-data/security-master`, built by dbt from the instruments products' published
  listings through declared input ports, never their landings: `asset` (no history), `instrument`
  and `venue_symbol` (type-2: valid from the first fetch that saw a version, `recorded_at` as
  knowledge time, the first version opening at `1970-01-01` so earlier trades still match, the
  open row's `valid_to` at `9999-12-31 23:59:59`, never null). Matching is by rule: upper-cased
  codes, a reviewed `seeds/code_exceptions.csv`, `seeds/stablecoins.csv` for the asset kind, the
  currency master for fiat. A spelling claimed twice is held for that venue only.
- `market-analytics/canonical-trades`, written now as a union over its declared venue input
  ports with Kraken the only one, so the follow-on plan only adds ports (plan review section 1).
- `market-analytics/candles` at every timeframe the enriched product and the demo need, 1m, 5m,
  1h and 1d, here rather than with the other venues (F15), with the `delay` policy on
  `delayed_reader` that replaces the retired `delayed-candles`; each touched bucket is recomputed
  in the order the archived `ohlcv_1m.sql` used.
- Derived products run on their own cron, read audited inputs past a watermark, record each
  input's state, snapshot and lag, and show `complete` or `partial: <inputs>`.
- Grants and expectations: `svc.partner-dashboard` as `delayed_reader` on `silver_trade` (rows
  at least 20 minutes old; `conn_id` and `conn_msg_seq` masked; bronze and the held table
  denied).

**Operations**

- **Maintenance per product**: compaction, orphan cleanup with a 24-hour floor, whole-table
  checks, and expiry past the rollback reach and the 7-day `as_of` window, under a per-product
  lock; freshness alerts skip it. A task in each DAG or one maintenance DAG walking the products
  is the owner's choice here, default kept as written (plan review section 6).
- **Operator hold**: `make hold PRODUCT=... REASON=...` pauses the DAG and writes a governance row
  the page reads; `make unhold` ends it; no deploy overrides it.
- `make product-verify` (offsets accounted, layers level, a second run adds nothing),
  `make rebuild` (bronze from the landing, silver and gold by `dbt build --full-refresh`) and a
  generated `make restore`: a `restore` table registers its copy; a `rebuild` table's entry is
  dropped without purging, recreated and rebuilt.
- **Proofs on the new tables**: `make parity-silver` recomputes silver from the landing with
  DuckDB (its lighter form, accounting plus unit tests plus the determinism check, is the owner's
  choice here, default kept); `make parity-ohlcv` re-pinned on Kraken (F16). Parity and the
  replay export run in the tools container with the read key; the replay export reads the Kraken
  landing.
- The lake dashboard and the alerts stood down in `000` return as the generated per-product
  ones; notebooks, labs and the release-check skill read the new names.
- **The 48-hour soak**: five-minute runs of the whole chain through Airflow, measuring the
  contention `000` could not: p50 and p90 with n per step, published through
  `/benchmark-report`. A chain p90 over 200 s takes the escapes in order: one Spark session for
  the venue connectors, Spark Connect, derived products every 15 minutes.
- **The first restore drill**, from the backup set, on volumes that are disposable before 3.0;
  it needs `LAKEKEEPER_ENCRYPTION_KEY` from where the owner keeps it.
- **Landing retention** as the owner decides; with option (a) the landing is never expired and
  the soak's disk growth is reported in the benchmark.

**Learning aids and documents.** dbt labs 4 (`UNNEST WITH ORDINALITY` and window flags) and 5
(type-2 history from listings). Runbooks: onboard a Kafka product; onboard a public-API product;
pause, resume, deprecate and retire; hold a product; accept data loss; backup, restore and
rebuild from empty. Chapter 23 (what runs, and crypto as products).

## Verification

```bash
bash scripts/chaos/kafka-connector-kill.sh          # resumes exactly once
bash scripts/chaos/kafka-below-log-start.sh         # fails loudly; the accept-data-loss run records offset_gap
make product-verify PRODUCT=kraken-trades
make parity-silver PRODUCT=kraken-trades
make parity-ohlcv                                    # on Kraken, the new pin
bash scripts/chaos/unknown-symbol.sh                # held, alert, instruments run, other symbols publish
bash scripts/chaos/bronze-failed-then-repaired.sh   # the repaired batch reaches silver
bash scripts/chaos/venue-test-fails.sh              # canonical-trades and candles unchanged
bash scripts/chaos/pause-input.sh kraken-trades      # canonical-trades runs and shows partial
make access-verify                                   # delayed_reader: nothing newer than 20 min, conn_id null
docker exec k2-trino trino --execute "SELECT count(*) FROM market_analytics.candles.ohlcv_1d"   # as a consumer
make hold PRODUCT=kraken-trades REASON=drill && make unhold PRODUCT=kraken-trades
bash scripts/verify/delete-files.sh                 # after 48 h: every silver table under the bound
make restore-drill                                   # passes and prints its time
make test
make check-docs
```

A unit test covers a trade earlier than its symbol's first fetch, which must still resolve
through the version that opens at `1970-01-01`. The soak's benchmark document carries the
command for every number it publishes.
