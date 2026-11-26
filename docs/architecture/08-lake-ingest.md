# 08. Lake ingest (retired)

This chapter described the v3 lake ingest: one Spark job, started by Airflow every 5 minutes, read
Redpanda by explicit offset range, appended every record verbatim to the lake's raw table with the
consumed offsets committed in the same Iceberg snapshot, and derived bronze, silver and gold from
that archive, audited nightly. It retired on 2026-11-26
([ADR-056](../adr/ADR-056-unified-data-product-platform.md), phase 000 of the
[platform plan](../plans/2026-11-26-unified-product-platform/README.md)): its tables were dropped
after a backup, and the chapter is archived unchanged at
[`legacy/v3-lake-ingest/docs/architecture/08-lake-ingest.md`](../../legacy/v3-lake-ingest/docs/architecture/08-lake-ingest.md),
beside the code, DAGs and runbooks under [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/).
[Chapter 20](20-target-data-product-platform.md) is the target that replaces it, phase by phase: a
connector and a verbatim landing per product, the first a file in phase 002 and the first Kafka
topic, Kraken trades, in phase 004.
