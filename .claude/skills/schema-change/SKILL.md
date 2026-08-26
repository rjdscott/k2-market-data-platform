---
name: schema-change
description: Move a data contract across every place it lives — Avro schema, ClickHouse DDL, Iceberg DDL, offload column lists, docs and tests — in one PR. Use when adding or changing a field on a trade/candle/book record, adding an exchange, changing a type or precision, renaming a column, or when the user says "add a field", "change the schema", "the Iceberg table doesn't match ClickHouse".
---

# schema-change — move the contract in lockstep

A K2 record exists in five places. Change fewer than all of them and the
failure is silent: the offload writes to Iceberg by column name, so a mismatch
surfaces as missing data hours later, not as a build error.

## The five places

| # | Artifact | Path |
|---|----------|------|
| 1 | Avro schema | `schemas/avro/*.avsc` |
| 2 | ClickHouse DDL | `docker/clickhouse/ddl/01-k2-schema.sql` |
| 3 | Iceberg DDL | `docker/iceberg/ddl/0{2,3,4}-*.sql` |
| 4 | Offload column list | `docker/offload/` — the `--columns` argument and the Prefect flow that passes it |
| 5 | Docs | `docs/architecture/schema-design.md`, `docs/architecture/partitioning-strategy.md` |

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
3. **ClickHouse DDL** — edit `01-k2-schema.sql`. It must stay **idempotent**:
   `CREATE TABLE IF NOT EXISTS` for new tables, `ALTER TABLE … ADD COLUMN IF
   NOT EXISTS` for new fields on existing ones. It re-runs on every container
   start. Apply to the running stack:
   ```bash
   docker exec -i k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" \
     --multiquery < docker/clickhouse/ddl/01-k2-schema.sql
   ```
   Materialized views do **not** pick up new columns — an MV that must carry
   the field is dropped and recreated, and that gap loses rows unless you
   backfill. Say so in the PR.
4. **Iceberg DDL** — edit the matching `docker/iceberg/ddl/*.sql`. **Column
   names and types must match ClickHouse exactly**; the offload appends
   positionally-by-name with no transform. Apply:
   ```bash
   docker exec k2-spark-iceberg bash /home/iceberg/ddl/00-run-all-ddl.sh
   ```
5. **Offload** — update the `--columns` list wherever it is passed, in the
   same order as both DDLs. Grep for every occurrence; there is more than one.
6. **Docs** — `schema-design.md` (the field table) and, if partitioning or
   sort order moved, `partitioning-strategy.md`.
7. **Tests** — a test that would fail if the field were dropped:
   `make test-kotlin` for normalizer changes, `make test-python` for offload
   changes.
8. **Verify end to end** on the running stack before opening the PR:
   ```bash
   docker exec k2-clickhouse clickhouse-client --password "$CLICKHOUSE_PASSWORD" -q \
     "DESCRIBE k2.silver_trades"
   docker exec k2-spark-iceberg spark-sql -e "DESCRIBE cold.silver_trades"
   # counts agree after one offload cycle
   ```

## One PR

All of it lands together, titled `feat(schema): …`. A half-migrated contract
is the worst state this repo can be in — the pipeline keeps running and quietly
drops the column.

## Rollback

ClickHouse `ADD COLUMN` is reversible (`DROP COLUMN`) and cheap. **Iceberg is
not**: files written under the new schema stay. Rolling back means dropping the
target Iceberg table and re-offloading from the rewound watermark
(`docs/operations/runbooks/iceberg-offload-watermark-recovery.md`) — so verify
step 8 before merging, not after.
