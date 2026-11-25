# docker/trino

One Trino node, the `trino` service in `docker-compose.yml`: SQL over the Iceberg lake
through the Lakekeeper REST catalog, for consumers and for the dbt marts of plan phase P3.
Spark keeps everything from raw to gold; Trino reads every namespace and writes only
`marts` ([ADR-035](../../docs/adr/ADR-035-trino-and-dbt-for-marts.md)).

`etc/` is mounted read-only over the image's `/etc/trino`. A changed file needs
`docker compose up -d --force-recreate --no-deps trino`; Trino reads its configuration
only at start.

| File | What it sets |
|---|---|
| `etc/config.properties` | one node: coordinator and its only worker; HTTP on 8080 inside the network |
| `etc/jvm.config` | heap `-Xms2816M -Xmx2816M` under the 4G limit, JVM time zone `UTC` |
| `etc/node.properties` | environment `k2`, data directory |
| `etc/catalog/lake.properties` | the Iceberg REST catalog: warehouse `k2`, static MinIO keys from `${ENV:MINIO_ROOT_USER}` / `${ENV:MINIO_ROOT_PASSWORD}`, path-style, region `local-01`, table locations assigned by Lakekeeper |
| `etc/access-control.properties`, `etc/rules.json` | file-based access control, below |
| `memory-lake.properties` | not used by the stack: `make test-trino` mounts it over `catalog/lake.properties` |

Left out of the image's defaults on purpose: its 2GB filesystem memory cache, which would
not fit beside the heap, and the `jmx`, `memory`, `tpch` and `tpcds` catalogs.

**Heap.** 2816M of the 4 GiB limit leaves 1.25 GiB for metaspace, the 256M code cache,
thread stacks and direct buffers. A single query ran at 1 CPU / 2 GiB with a 1280M heap
in the spike; the larger limit is for dbt running several models at once (measured
below).

**Time zone.** The JVM is pinned to UTC, which is the session zone of a client that sends
none. A client that sends its own zone gets that zone: the Trino CLI sends the zone of the
machine it runs on. Pass `--timezone UTC` to the CLI, and set it in the dbt profile.

## Access rules

`etc/rules.json`, first match wins:

- catalog `lake`: every table readable by every user (`SELECT`, `GRANT_SELECT`, so a view
  in `marts` can read gold); schema `marts` owned by every user, its tables read-write
  (create, insert, delete, update, alter, drop, views);
- every other schema in `lake`: no `CREATE SCHEMA`, `DROP SCHEMA`, `CREATE TABLE`,
  `CREATE VIEW`, insert, delete, update, alter, rename or drop;
- no `CALL` of the Iceberg procedures (`register_table`, `unregister_table`): with no
  procedure rules, only Trino's built-in procedures run;
- catalog `system` read-only; cluster information readable, not writable (no node shutdown).

**Identity is asserted, not authenticated.** Trino takes the user from the `X-Trino-User`
header, so a rule that named a user would grant its rights to anyone who typed that name.
The rules name none. The port is published on `127.0.0.1` only, like Airflow's. The rules
are guard rails against mistakes, not a security boundary: Lakekeeper is unauthenticated
too ([ADR-028](../../docs/adr/ADR-028-non-goals-and-honest-limits.md) section 8), so
anything on this host can change the catalog without Trino.

**What the rules do not cover: `ALTER TABLE ... EXECUTE`.** In Trino 484 the file-based
access control allows every table procedure on every table:
`checkCanExecuteTableProcedure` is an empty method in both `FileBasedSystemAccessControl`
(line 1043) and `FileBasedAccessControl` (line 727) of `lib/trino-plugin-toolkit` at tag
484. So `optimize`, `optimize_manifests`, `drop_extended_stats`, `expire_snapshots`,
`remove_orphan_files` and `rollback_to_snapshot` run on `raw`, `bronze`, `silver`, `gold`
and `audit` through Trino. What limits them:

- `expire_snapshots` and `remove_orphan_files` refuse a retention under 7 days, Trino's
  default `iceberg.expire-snapshots.min-retention` and
  `iceberg.remove-orphan-files.min-retention`, the same floor `maintenance.py` keeps;
- `add_files` and `register_table` are off (`iceberg.add-files-procedure.enabled` and
  `iceberg.register-table-procedure.enabled` default to false);
- `rollback_to_snapshot` and `optimize` are not limited. A rollback of `raw.messages`
  would move `main` back while the ingest still resumes from the newest ingest snapshot on
  any ref (`docker/lake/offsets.py`), so the offsets after the rollback point would never
  be read again. Never run a table procedure through Trino outside `marts`; Spark's
  `maintenance.py` is the only maintenance path.

ADR-035 records the gap, the escape hatch and the trigger to close it.

Proof on the live lake, with nothing written. `EXPLAIN` runs the access checks of a DML
statement and executes nothing:

```bash
docker exec k2-trino trino --execute 'EXPLAIN INSERT INTO lake.gold.ohlcv_1m SELECT * FROM lake.gold.ohlcv_1m LIMIT 0'
#   ... failed: Access Denied: Cannot insert into table lake.gold.ohlcv_1m
docker exec k2-trino trino --execute 'EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize'
#   a plan with TableExecute[]: the table procedure passes access control
```

## Verified 2026-11-25

Image `trinodb/trino:484` (release 484, 15 Oct 2026; digest
`sha256:2704eb53935af5e582d63d6033e9bf99d20c325e9b0e30b1016ef59a4773979f`) against
Lakekeeper v0.13.9 and MinIO on the running stack, as throwaway containers on the compose
network (`k2-trino-spike`, then `k2-trino-check` with this directory as committed):

| Check | Result |
|---|---|
| Namespaces and tables through Lakekeeper, static keys | listed; first lake query 9.7 s after `docker run` at 2 CPU / 4 GiB (20 to 23 s at 1 CPU) |
| Read `gold.trades`, `gold.ohlcv_1m`, `raw.messages`, `silver.book_*` | works; `timestamp(6) with time zone`, `decimal(38,10)`, `map(varchar, varbinary)`, `array(row(...))` all map |
| `FOR VERSION AS OF <snapshot id>` on `gold.ohlcv_1m` | works |
| Writes in a scratch namespace (spike, before the rules): `CREATE SCHEMA`, CTAS, INSERT, `DROP` | Lakekeeper accepts every commit; `DROP TABLE` is a purge drop, and Lakekeeper's `tabular_purge` task deletes the files at once |
| Iceberg REST view, create and read back | works; stored with dialect `trino` |
| A Trino-written table read by DuckDB 1.4.4 and Spark 3.5.5 | both read it (gzipped `*.gz.metadata.json` included); Spark reads a Trino view only when its SQL also parses as Spark SQL; DuckDB does not list views |
| With the rules: `CREATE TABLE lake.gold.*`, `CREATE SCHEMA`, `EXPLAIN` of an INSERT, DELETE or CTAS into gold, `CALL lake.system.unregister_table` | `Access Denied` for each; no Trino-written snapshot in any of the 26 tables afterwards |
| Session time zone | `UTC` from a client that sends none |
| Idle, 2 CPU / 4 GiB | 1.42 GiB, about 3% CPU |
| The `gold.candles()` aggregation over all of `gold.trades`, read only | 6.6 s alone; four at once 12.5 s each at most, peak 2.86 GiB of 4 GiB and 2.15 cores, no OOM |

`gold.candles()` ported verbatim fails with `Decimal overflow`: Trino types
`DECIMAL(38,10) * DECIMAL(38,10)` as `decimal(38,6)` but overflows once the exact
product passes about 1e18. A `DECIMAL(38,0)` product of the two `_e8` integers runs.

Snapshots Trino writes carry `engine-name`, `engine-version`, `trino_query_id` and
`trino_user`, and no `k2.*` property, so `offsets.latest_summary` skips them.

`make test-trino` (CI job `trino`) starts this `etc/` with no network to check that every
property is one Trino knows (a misspelt one fails the start with
`Configuration property '...' was not used`), then asserts the rules on a memory catalog
named `lake`.
