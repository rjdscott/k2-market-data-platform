# docker/lake

The lake's bootstrap and the Spark-side Python kept for reuse. Mounted into
`spark-iceberg` at `/home/iceberg/lake`, and read-only into the `lake-init` one-shot at
`/init`.

The old lake ingest pipeline that ran from here (the raw archive, per-venue bronze and
silver, gold, the audits, maintenance, rebuild, the lake DDL and its metrics exporter) is
retired ([ADR-056](../../docs/adr/ADR-056-unified-data-product-platform.md)) and archived
in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/). The sections that older
links to this file name are in the pipeline's own README, archived as
[`legacy/v3-lake-ingest/lake/README.md`](../../legacy/v3-lake-ingest/lake/README.md). Its
tables were dropped. Nothing here runs on a schedule now, and no live data lands in the lake
until the Kafka connector of phase 004
([plan](../../docs/plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)).

## What stays, and why

| File | What it is | Why it stays |
|------|------------|--------------|
| `init-lake.sh` | the `lake-init` one-shot: MinIO bucket, Lakekeeper bootstrap, warehouse `k2`, then every warehouse of the release's storage plan (`compiled/lake/storage-plan.json`, read through the trino pointer, or the release `make deploy` names in `K2_STORAGE_PLAN`; ADR-044). It creates no namespaces | the domain catalogs Trino reads need the warehouses |
| `spark_conf.py` | `lake_session()`, the one `lake` catalog config; `--smoke` round-trips one row in its own namespace and drops both | the session any Spark job on the lake opens |
| `offsets.py` | exactly-once offset bookkeeping, pure | the plan reuses it for the phase 004 Kafka connector |
| `wire.py` | the Confluent framing, as a Python parser and as Spark SQL | its framing half is reused in phase 004, which splits it from the trade constants |
| `catalog.py` | snapshots on `main`'s lineage, the broker's offsets, the registry fetch by id | the Spark side of `offsets.py` and `wire.py`; `books.py` imports two of them |
| `bars.py` | event bars as SQL in two dialects, with a pure Python reference | kept for reuse, as the plan says |
| `books.py`, `book.py`, `instruments.py` | the book replay, its pure core, the registry loader it uses | kept for reuse, as the plan says; every table `books.py` names is retired |

Plan [000](../../docs/plans/2026-11-26-unified-product-platform/000-clear-the-ground.md),
Wave 3, step 5, names what stays. The tests are `tests/test_lake_*.py`, `tests/test_bars.py`
and `tests/test_wire_format.py` (`make test-python`).
