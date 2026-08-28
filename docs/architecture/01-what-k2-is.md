# 01. What K2 is, and what it is not

> **You will learn** what the platform is for, what it deliberately is not, and the six rules the design is held to.
> **Read this if** you read nothing else; everyone, first.
> **Before this** nothing.

## What it is

K2 is a market data platform for quantitative research. It takes live trades and order
books from three crypto exchanges over public WebSocket feeds, archives every frame
verbatim to an Iceberg lake, derives typed and canonical layers from that archive, and
serves the canonical layer from ClickHouse for dashboards and fast queries. It runs on one
host inside a declared budget of 16 CPU / 40 GB: as deployed, 15 long-running services at
14.60 CPU / 25.625 GiB of limits, plus four one-shot init containers
([docker-resources.md](../operations/docker-resources.md)).

Three properties define it, and the rest of the book is their consequences:

1. **The lake is the record.** `raw.messages` holds every byte received and is never
   expired; bronze, silver, gold and ClickHouse are functions of it and can be rebuilt
   ([09](09-lake-layers.md), [ADR-018](../adr/ADR-018-v3-lake-first-rust-capture.md)).
2. **Correctness is proven, not asserted.** Sequence and checksum checks at capture,
   per-layer audits nightly, three-way candle parity at a pinned snapshot, chaos scripts
   with measured recovery ([11](11-observability.md)).
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
- **Not highly available.** One broker, one ClickHouse, one Spark, one host. Recovery is a
  container restart, measured in tens of seconds by chaos scripts
  ([16](16-failure-modes.md)); nothing survives a dead disk.
- **Not full depth, not L3, not multi-asset.** Top-20 L2 at 1 Hz and trades, crypto only.
  Deeper books can be replayed from raw; no equities or futures path is designed.
- **Not a query service.** Reads are SQL against ClickHouse or DuckDB over the lake. There
  is no API, no Alertmanager routing, and no load test above 1x.

## Where it sits

| Tier | Owns | Freshness | Rebuildable from |
|---|---|---|---|
| Capture (Rust, one process per venue) | receiving, stamping, publishing | live | the venue only |
| Redpanda | 48 h raw buffer, 7 d derived | live | the venue only |
| Iceberg lake | the record and every derived layer | 5 min | `raw.messages` |
| ClickHouse `gold` | serving the canonical layer | seconds from the topics | lake gold |
| Notebooks (DuckDB) | research over the lake | on demand | n/a |

Two consumers read the same topics: ClickHouse for the head, the lake for the record. If
they ever disagree, the lake wins ([10](10-clickhouse-gold.md)).

## Fits and does not fit

**Fits.** Intraday and historical research over every trade and a 1 Hz top-20 book,
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
2. **Idempotency over coordination.** Retries, replays and reloads must be free. Enforced by
   offsets committed inside the Iceberg snapshot ([08](08-lake-ingest.md)) and
   `ReplacingMergeTree` keys in ClickHouse ([10](10-clickhouse-gold.md)).
3. **Raw survives normalisation.** Nothing decodes before it is archived. Enforced by
   `RawMessage` produced first for every frame ([05](05-capture.md)) and `raw.messages`
   with no expiry ([09](09-lake-layers.md)).
4. **Isolate at the blast radius.** One capture process per venue; a venue outage takes one
   container. Enforced by the compose topology and `scripts/chaos/capture-kill.sh`
   (`CaptureDown` fired at 119 s under a 150 s kill, `scripts/chaos/results/2026-08-26.tsv`).
5. **Use what is running before adding something.** ClickHouse consumes the topics itself;
   Spark is batch only; Prefect is the one scheduler. Enforced by
   [ADR-004](../adr/ADR-004-eliminate-spark-streaming.md) and
   [ADR-008](../adr/ADR-008-eliminate-prefect-orchestration.md), whose `## Outcome`
   records the reversal on the record.
6. **Instrument it, then say what is not instrumented.** 28 alert rules as code, chaos
   results as files, and a stated list of gaps: no Alertmanager, 22 of 28 rules carry a
   runbook annotation, 17 of 28 have a unit test ([11](11-observability.md)).

**Applied to a new component.** Declare its limits; make its writes idempotent; keep its
input verbatim somewhere rebuildable; give it its own container; ask whether something
already running can do the job; export a staleness timestamp and write the alert and its
runbook in the same PR.

## Key points

- A research platform on public feeds: complete, provable, reproducible; not fast enough
  to trade on and not built to stay up through hardware loss.
- The lake is the record; ClickHouse, the products and the notebooks are disposable.
- Sixteen CPUs and forty gigabytes are a stated constraint, and CI enforces the
  declaration.
- Every principle names the file, test or alert that holds it; every number names its
  benchmark.
