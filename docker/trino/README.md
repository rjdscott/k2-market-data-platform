# docker/trino

One Trino node, the `trino` service in `docker-compose.yml`: SQL over the Iceberg lake
through the Lakekeeper REST catalog, for consumers and for the dbt marts of plan phase P3.
Spark keeps everything from raw to gold. Trino has two catalogs
([ADR-035](../../docs/adr/ADR-035-trino-and-dbt-for-marts.md)):

| Catalog | Lakekeeper warehouse | Files under | Trino may |
|---|---|---|---|
| `lake` | `k2`: `raw`, `bronze`, `silver`, `gold`, `audit` | `s3://k2-lake/warehouse/k2/` | read, time travel; nothing that commits |
| `marts` | `marts`, created by `docker/lake/init-lake.sh`, no schema yet (dbt creates them) | `s3://k2-lake/warehouse/marts/` | read and write: tables, views, schemas |

`etc/` is mounted read-only over the image's `/etc/trino`. A changed file needs
`docker compose up -d --force-recreate --no-deps trino`; Trino reads its configuration
only at start.

| File | What it sets |
|---|---|
| `etc/config.properties` | one node: coordinator and its only worker; HTTP on 8080 inside the network |
| `etc/jvm.config` | heap `-Xms2816M -Xmx2816M` under the 4G limit, JVM time zone `UTC` |
| `etc/node.properties` | environment `k2`, data directory |
| `etc/catalog/lake.properties` | Iceberg REST catalog, warehouse `k2`, **`iceberg.security=read_only`**; static MinIO keys from `${ENV:MINIO_ROOT_USER}` / `${ENV:MINIO_ROOT_PASSWORD}`, path-style, region `local-01` |
| `etc/catalog/marts.properties` | the same over warehouse `marts`, writable, table locations assigned by Lakekeeper |
| `etc/access-control.properties`, `etc/rules.json` | file-based access control at the catalog level, below |
| `test/` | not used by the stack: the catalogs `make test-trino` mounts in place of `etc/catalog/` |

Left out of the image's defaults on purpose: its 2GB filesystem memory cache, which would
not fit beside the heap, and the `jmx`, `memory`, `tpch` and `tpcds` catalogs.

**Heap.** 2816M of the 4 GiB limit leaves 1.25 GiB for metaspace, the 256M code cache,
thread stacks and direct buffers. A single query ran at 1 CPU / 2 GiB with a 1280M heap
in the spike; the larger limit is for dbt running several models at once (measured
below).

**Time zone.** The JVM is pinned to UTC, which is the session zone of a client that sends
none. A client that sends its own zone gets that zone: the Trino CLI sends the zone of the
machine it runs on. Pass `--timezone UTC` to the CLI, and set it in the dbt profile.

## Why `lake` is read-only at the connector

Two layers, because the second alone has a hole:

1. **`iceberg.security=read_only` on catalog `lake`.** The connector refuses every write,
   DDL and `ALTER TABLE ... EXECUTE` (table procedures: `optimize`, `rollback_to_snapshot`,
   `expire_snapshots`, `remove_orphan_files` and the rest). This is the layer that keeps
   Trino from committing to the lake.
2. **`etc/rules.json`:** `marts` all, `lake` and `system` read-only, cluster information
   readable, no Iceberg `CALL` procedures (with no procedure rules only Trino's built-in
   ones run).

The file-based rules alone do not cover table procedures. In Trino 484
`checkCanExecuteTableProcedure` is an empty method in both `FileBasedSystemAccessControl`
(line 1043) and `FileBasedAccessControl` (line 727) of `lib/trino-plugin-toolkit`, and on
2026-11-25, with the rules and no connector setting,
`EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize` planned with no denial. A
`rollback_to_snapshot` of `raw.messages` would move `main` back while the ingest resumes
from the newest ingest snapshot on any ref (`docker/lake/offsets.py`), so the offsets after
it would never be read again. Hence the connector setting, and marts in a warehouse of
their own so that the lake's catalog can be read-only.

**Identity is asserted, not authenticated.** Trino takes the user from the `X-Trino-User`
header, so a rule that named a user would grant its rights to anyone who typed that name.
The rules name none. The port is published on `127.0.0.1` only, like Airflow's. Both
layers are guard rails against mistakes, not a security boundary: Lakekeeper is
unauthenticated ([ADR-028](../../docs/adr/ADR-028-non-goals-and-honest-limits.md) section 8)
and Trino holds the MinIO root keys, so anything on this host can change the lake without
Trino. A read-only storage key for Trino on `warehouse/k2/` is the named follow-up in
ADR-035.

`make test-trino` (CI job `trino`) checks all of it without a stack: the real `etc/`
starts and `lake` is `read_only`; on a local Iceberg catalog that is read-only at the
connector, every table procedure and write is denied while the writable twin runs
`optimize`; on memory catalogs named `lake` and `marts`, the rules deny writes in `lake`
and allow them in `marts`.

## Verified 2026-11-25

Image `trinodb/trino:484` (release 484, 15 Oct 2026; digest
`sha256:2704eb53935af5e582d63d6033e9bf99d20c325e9b0e30b1016ef59a4773979f`) against
Lakekeeper v0.13.9 and MinIO on the running stack, as throwaway containers on the compose
network (`k2-trino-spike`, then `k2-trino-check` with this directory as committed):

| Check | Result |
|---|---|
| Namespaces and tables through Lakekeeper, static keys | listed; first lake query 9.7 s after `docker run` at 2 CPU / 4 GiB (20 to 23 s at 1 CPU) |
| Read `gold.trades`, `gold.ohlcv_1m`, `raw.messages`, `silver.book_*` | works; `timestamp(6) with time zone`, `decimal(38,10)`, `map(varchar, varbinary)`, `array(row(...))` all map |
| `FOR VERSION AS OF <snapshot id>` on `lake.gold.ohlcv_1m` | works through the read-only catalog |
| Catalog `lake`: `EXPLAIN ALTER TABLE ... EXECUTE optimize`, `ALTER TABLE lake.gold.ohlcv_1m EXECUTE rollback_to_snapshot(<current main>)` | `Access Denied: Cannot execute table procedure OPTIMIZE` / `ROLLBACK_TO_SNAPSHOT on gold.ohlcv_1m`; `main` unchanged |
| Catalog `lake`: `CREATE TABLE`, `INSERT`, `DELETE`, `CREATE SCHEMA`, `CALL lake.system.unregister_table` | `Access Denied` for each; no Trino-written snapshot in any of the 26 tables of warehouse `k2` afterwards |
| Catalog `marts`: schema, CTAS of 3,000 rows from `lake.gold.ohlcv_1m`, a view over `lake.gold.ohlcv_1m` with a 20-minute delay | all work; table and view stored under `s3://k2-lake/warehouse/marts/<uuid>`, metadata gzipped (`*.gz.metadata.json`); the view's SQL names `lake.gold.ohlcv_1m`, dialect `trino` |
| The `marts` table read elsewhere | DuckDB 1.4.4 (`ATTACH 'marts' AS marts` beside `ATTACH 'k2' AS lake`) and Spark 3.5.5 (a second `SparkCatalog` named `marts`, warehouse `marts`) both read it; Spark also read the view, whose SQL is valid Spark SQL and names a catalog Spark also calls `lake`; DuckDB does not list views |
| `DROP TABLE`, `DROP VIEW`, `DROP SCHEMA` in `marts` | a purge drop: Lakekeeper's `tabular_purge` task deleted the files; `warehouse/marts/` held 0 objects within seconds |
| Snapshot summary of a Trino write | `engine-name`, `engine-version`, `trino_query_id`, `trino_user`; no `k2.*` property |
| Session time zone | `UTC` from a client that sends none, also with `TZ=Australia/Sydney` in the container |
| Idle, 2 CPU / 4 GiB | 1.42 GiB, about 3% CPU |
| The `gold.candles()` aggregation over all of `gold.trades`, read only | 6.6 s alone; four at once 12.5 s each at most, peak 2.86 GiB of 4 GiB and 2.15 cores, no OOM |

`gold.candles()` ported verbatim fails with `Decimal overflow`: Trino types
`DECIMAL(38,10) * DECIMAL(38,10)` as `decimal(38,6)` but overflows once the exact
product passes about 1e18. A `DECIMAL(38,0)` product of the two `_e8` integers runs.

Proof commands for the live service, with nothing written:

```bash
docker exec k2-trino trino --execute 'EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize'
#   ... failed: Access Denied: Cannot execute table procedure OPTIMIZE on gold.ohlcv_1m
docker exec k2-trino trino --execute 'EXPLAIN INSERT INTO lake.gold.ohlcv_1m SELECT * FROM lake.gold.ohlcv_1m LIMIT 0'
#   ... failed: Access Denied: Cannot insert into table lake.gold.ohlcv_1m
```
