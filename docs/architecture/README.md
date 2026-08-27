# Architecture: the book

Numbered chapters, in reading order. Start at [00](00-start-here.md).

| # | Chapter | Read it for |
|---|---|---|
| 00 | [Start here](00-start-here.md) | how the book is organised, three reading paths |
| 01 | [What K2 is](01-what-k2-is.md) | purpose, non-goals, principles, what it is deliberately wrong for |
| 02 | Market data concepts | books, sequences, checksums, timestamps, fixed point, OHLCV/BBO, *next* |
| 03 | Data engineering concepts | exactly-once, snapshots, medallion, evolution, pruning, *next* |
| 04 | [System overview](04-system-overview.md) | the diagram, three invariants, one section per tier |
| 05 | [Capture](05-capture.md) | one Rust process per venue: frame loop, sink, image |
| 06 | [Capture, venues](06-capture-venues.md) | Binance, Kraken, Coinbase dialects; sequencing, checksum, resync |
| 07 | [Wire contracts](07-wire-contracts.md) | topics, Avro records, keying, retention, registry |
| 08 | [Lake ingest](08-lake-ingest.md) | Redpanda to Iceberg exactly once; incremental layers; audits |
| 09 | [Lake layers](09-lake-layers.md) | raw, bronze, silver, gold, what each holds and why |
| 10 | [ClickHouse gold](10-clickhouse-gold.md) | dedup contract, candles on read, reload from the lake |
| 11 | [Observability](11-observability.md) | metrics, 28 rules with runbooks and tests, chaos |
| 12 | [Data strategy](12-data-strategy.md) | why four layers; what ClickHouse keeps; retention vs disk |
| 13 | [Schema design](13-schema-design.md) | every column, every layer, the wire contracts |
| 14 | [Partitioning strategy](14-partitioning-strategy.md) | partitions, sort orders, file sizes, ClickHouse keys |
| 15 | [Capacity model](15-capacity-model.md) | predicted vs measured bytes/day, CPU, disk runway |
| 16 | [Failure modes](16-failure-modes.md) | FMEA with detection, blast radius, recovery, proof |
| 17 | [Scale-out path](17-scale-out-path.md) | AWS mapping at TB/PB, designed, not exercised |
| A1 | [Technology stack](A1-technology-stack.md) | versions and the ADR behind each |

Chapters 02 and 03 are the next PR. Decisions live in [`../adr/`](../adr/README.md),
numbers in [`../benchmarks/`](../benchmarks/README.md), procedures in
[`../runbooks/`](../runbooks/README.md).
