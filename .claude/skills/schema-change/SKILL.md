---
name: schema-change
description: Move a data contract across every place it lives — Avro schema, ClickHouse DDL, Iceberg lake DDL, docs and tests — in one PR. Use when adding or changing a field on a trade/candle/book record, adding an exchange, changing a type or precision, renaming a column, or when the user says "add a field", "change the schema", "the Iceberg table doesn't match ClickHouse".
---

# schema-change — move the contract in lockstep

A K2 record exists in five places. Change fewer than all of them and the
failure is silent: the lake ingest decodes Avro into `bronze.*` by field name,
so a mismatch surfaces as a null column hours later, not as a build error.

## The five places

| # | Artifact | Path |
|---|----------|------|
| 1 | Avro schema | `schemas/avro/*.avsc` |
| 2 | Lake DDL (`raw`/`bronze`/`audit`) | `docker/lake/ddl/lake.sql` |
| 3 | Ingest decode | `docker/lake/ingest.py` — the stage-2 select list |
| 4 | ClickHouse DDL (derived hot tier) | `docker/clickhouse/ddl/10-gold-tables.sql` + `20-gold-kafka.sql` |
| 5 | Docs | `docs/architecture/13-schema-design.md`, `docs/architecture/14-partitioning-strategy.md` |

## Checklist

Work top to bottom; do not skip a row because "nothing reads it yet".

1. **Decide compatibility.** Additive nullable field → safe. Type change,
   rename, or removal → breaking; it needs an `/adr` and a stated migration
   for existing Iceberg data, because Iceberg keeps the old files.
2. **Avro** — edit the `.avsc`. Check `logicalType` is nested *inside* the
   type union, not a sibling of `type` (that mistake is already in this repo's
   history and silently does nothing). Then check registry compatibility
   before deploying:
   ```bash
   curl -s -X POST http://localhost:8081/compatibility/subjects/<subject>/versions/latest \
     -H 'Content-Type: application/vnd.schemaregistry.v1+json' \
     -d "{\"schema\": $(jq -Rs . < schemas/avro/<file>.avsc)}" | jq
   # {"is_compatible": true}  — anything else stops the change
   curl -s http://localhost:8081/subjects | jq   # subject names, if unsure
   ```
3. **Lake DDL** — edit `docker/lake/ddl/lake.sql`. `raw.messages` is frozen;
   `bronze.*` takes **nullable additions only** (`ALTER TABLE … ADD COLUMN`),
   because Iceberg keeps the files written under the old schema. Apply:
   ```bash
   docker exec k2-spark-iceberg python3 /home/iceberg/lake/apply_ddl.py
   ```
   Then update the stage-2 select list in `docker/lake/ingest.py` so the new
   field is actually written, and `tests/test_wire_format.py`, which parses
   `lake.sql` and asserts the two agree.
4. **ClickHouse DDL** — the served `gold` tier, two files, both idempotent
   (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN IF NOT EXISTS`);
   they are mounted at `/docker-entrypoint-initdb.d` and run on a fresh
   container start. `10-gold-tables.sql` is the contract and the only one CI
   applies; `20-gold-kafka.sql` attaches the Avro queue tables and their MVs.
   **A field added to the Avro schema must be added to the queue table too** —
   AvroConfluent maps by name and a mismatch either stalls the feed into
   `gold.feed_errors` or errors on every record. `tests/test_wire_format.py`
   asserts both directions; run it.
   ```bash
   docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
     --multiquery < docker/clickhouse/ddl/10-gold-tables.sql
   docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
     --multiquery < docker/clickhouse/ddl/20-gold-kafka.sql
   ```
   Materialized views do **not** pick up new columns — an MV that must carry
   the field is dropped and recreated, and that gap loses rows unless you
   backfill from the lake ([clickhouse-rebuild-from-lake.md](../../../docs/runbooks/clickhouse-rebuild-from-lake.md)).
   Say so in the PR.

   The `k2.*` medallion (`01-k2-schema.sql`, `k2.silver_trades`) was dropped at
   the Phase E cutover and archived in `legacy/v2-clickhouse/`. It is not a
   place a contract lives any more.
5. **Docs** — `13-schema-design.md` (the field table) and, if partitioning or
   sort order moved, `14-partitioning-strategy.md`.
6. **Tests** — a test that would fail if the field were dropped:
   `make test-rust` for capture changes, `make test-python` for the contract
   and wire-format tests. (Not `make test-kotlin` — that tier retired in ADR-019.)
7. **Verify end to end** on the running stack before opening the PR:
   ```bash
   docker exec k2-spark-iceberg spark-sql -e "DESCRIBE lake.bronze.binance_trade"
   docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
     "DESCRIBE gold.trades"
   make lake-verify       # raw count == bronze count, double-run adds 0
   make test-clickhouse   # the gold DDL on a throwaway server, assertions and all
   ```

## One PR

All of it lands together, titled `feat(schema): …`. A half-migrated contract
is the worst state this repo can be in — the pipeline keeps running and quietly
drops the column.

## Rollback

ClickHouse `ADD COLUMN` is reversible (`DROP COLUMN`) and cheap. **Iceberg is
not**: files written under the new schema stay. Rolling back a `bronze.*` change
means dropping the table and rebuilding it from `raw.messages`, which is why
`raw` holds the payload verbatim (`docs/runbooks/lake-recovery.md`) — so verify
step 7 before merging, not after.
