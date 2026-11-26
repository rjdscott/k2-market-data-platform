# 01. What K2 is, and what it is not

> **You will learn** what the platform is for, what it deliberately is not, and the six rules the design is held to.
> **Read this if** you read nothing else; everyone, first.
> **Before this** nothing.

**Retired, 2026-11-26.** The v3 lake ingest is retired ([ADR-056](../adr/ADR-056-unified-data-product-platform.md),
phase 000 of the [platform plan](../plans/2026-11-26-unified-product-platform/README.md)): its DAGs, its raw,
bronze, silver, gold and audit tables and the dbt marts read from them were backed up and dropped, and its code,
tests and chapters 08 and 09 are archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/). No live
crypto data lands in the lake until phase 004; the captures still publish to Redpanda, and ClickHouse is paused
([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)). [Chapter 20](20-target-data-product-platform.md)
is the target that replaces it, phase by phase. This chapter's purpose, non-goals and principles stand; where it
says what the lake holds, it now says what the retired pipeline held.

## What it is

K2 is a market data platform for quantitative research. It takes live trades from three
crypto exchanges over public WebSocket feeds and publishes every frame, verbatim and decoded,
to Redpanda, with Trino for SQL over an Iceberg lake and Airflow 3 as the scheduler. Until
the retirement above, a Spark batch ingest archived every frame verbatim to the lake and
derived typed and canonical layers from that archive, and ClickHouse served the canonical
layer for dashboards and fast queries. It runs on one host inside a declared ceiling of
25 CPU / 48 GiB at the bootstrap peak ([ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md));
the services and their limits are what `scripts/check-budget.py` prints, per service in
[docker-resources.md](../operations/docker-resources.md). While the platform is built, the
order books are paused on every venue and K2 captures trades only
([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)).

Three properties define it, and the rest of the book is their consequences:

1. **The lake is the record.** In the retired ingest the raw table held every byte
   received and was never expired; bronze, silver, gold and ClickHouse were functions of it
   and could be rebuilt ([09](09-lake-layers.md), [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md)).
   The target keeps the rule per product: a verbatim landing first ([20](20-target-data-product-platform.md)).
2. **Correctness is proven, not asserted.** Sequence and checksum checks at capture and
   chaos scripts with measured recovery run today; the nightly per-layer audits retired
   with the ingest, and the candle parity and lake-verify gates print a SKIP naming phase
   004 ([11](11-observability.md)).
3. **Every number has a provenance.** Anything quoted in these pages cites the benchmark
   file and the command that produced it ([benchmarks](../benchmarks/README.md)).

## What it is not

The four below are the ones that change how you read the rest of the book. The full list —
twelve non-goals, each with its cost, a revisit trigger and the nearest thing K2 does offer —
is [ADR-028](../adr/ADR-028-non-goals-and-honest-limits.md).

- **Not a trading path.** Feeds are public and cross the internet; measured venue-to-receive
  latency is 42 ms p50 / 207 ms p99 on Binance and higher elsewhere
  ([benchmarks](../benchmarks/2026-08-27.md#latency--exchange-timestamp--k2-receive)).
  Nothing here is sub-millisecond, colocated, or deterministic enough to trade on.
- **Not highly available.** One broker, one Spark, one host. Recovery is a
  container restart, measured in tens of seconds by chaos scripts
  ([16](16-failure-modes.md)); nothing survives a dead disk.
- **Not full depth, not L3, not multi-asset.** Trades, and top-20 L2 at 1 Hz when book
  capture resumes, crypto only; no equities or futures path is designed.
- **Not a query service.** Reads are SQL through Trino over the lake. There is no API, no
  Alertmanager routing, and no load test above 1x.

## Where it sits

| Tier | Owns | Freshness | Rebuildable from |
|---|---|---|---|
| Capture (Rust, one process per venue) | receiving, stamping, publishing | live | the venue only |
| Redpanda | 48 h raw buffer, 7 d derived | live | the venue only |
| Iceberg lake | no live data until phase 004; the v3 layers are retired | n/a | each product's landing (target) |
| ClickHouse `gold` | paused ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)) | n/a | n/a while paused |
| Notebooks (DuckDB) | archived in `legacy/v3-lake-ingest/notebooks/` | n/a | n/a |

Until the retirement, two consumers read the same topics: ClickHouse for the head, the lake
for the record, and the lake won any disagreement ([10](10-clickhouse-gold.md)). Today
neither reads them; the lake's next consumer is phase 004's first product.

## Fits and does not fit

**Fits**, once the lake holds live data again. Intraday and historical research over every trade and a 1 Hz top-20 book,
cross-venue comparison on one `canonical_symbol`, live dashboards on candles computed on
read, and learning a lakehouse end to end: exactly-once ingest, medallion layers, table
maintenance, audits, all small enough to read.

**Does not fit.** Order execution or market making; real-time risk; anything needing
replication or failover; venues or asset classes beyond the three configured.

## Principles

Each rule names where it is enforced; a rule with no enforcement is a slogan.

1. **The budget is a constraint, not a goal.** Every service declares CPU and memory
   limits; CI fails if one is missing. Enforced by `.github/workflows/ci.yml` (compose job)
   and [ADR-010](../adr/ADR-010-resource-budget.md).
2. **Idempotency over coordination.** Retries, replays and reloads must be free. Enforced,
   while they ran, by offsets committed inside the Iceberg snapshot ([08](08-lake-ingest.md),
   retired; `docker/lake/offsets.py` is kept for the new connectors) and
   `ReplacingMergeTree` keys in ClickHouse ([10](10-clickhouse-gold.md), paused).
3. **Raw survives normalisation.** Nothing decodes before it is archived. Enforced by
   `RawMessage` produced first for every frame ([05](05-capture.md)); the lake's raw table,
   with no expiry, retired with the ingest ([09](09-lake-layers.md)).
4. **Isolate at the blast radius.** One capture process per venue; a venue outage takes one
   container. Enforced by the compose topology and `scripts/chaos/capture-kill.sh`
   (`CaptureDown` fired at 119 s under a 150 s kill, `scripts/chaos/results/2026-08-26.tsv`).
5. **Use what is running before adding something.** ClickHouse consumed the topics itself until its pause;
   Spark is batch only; Airflow is the one scheduler. Enforced by
   [ADR-004](../adr/ADR-004-eliminate-spark-streaming.md) and
   [ADR-008](../adr/ADR-008-eliminate-prefect-orchestration.md), whose `## Outcome`
   records the reversal on the record.
6. **Instrument it, then say what is not instrumented.** Alert rules as code in
   `docker/prometheus/rules/`, chaos results as files, and a stated list of gaps: no
   Alertmanager, every rule carries a runbook annotation (gate (d2) of `scripts/check-docs.sh`),
   and not every rule has a unit test ([11](11-observability.md)).

**Applied to a new component.** Declare its limits; make its writes idempotent; keep its
input verbatim somewhere rebuildable; give it its own container; ask whether something
already running can do the job; export a staleness timestamp and write the alert and its
runbook in the same PR.

## Key points

- A research platform on public feeds: complete, provable, reproducible; not fast enough
  to trade on and not built to stay up through hardware loss.
- The lake is the record; ClickHouse, the products and the notebooks are disposable. While
  the platform is rebuilt the lake holds no live data, ClickHouse is paused and the
  notebooks are archived.
- 25 CPU and 48 GiB at the bootstrap peak are a stated constraint, and CI enforces the
  declaration.
- Every principle names the file, test or alert that holds it; every number names its
  benchmark.
