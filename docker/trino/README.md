# docker/trino

One Trino node, the `trino` service in `docker-compose.yml`: SQL over the Iceberg lake
through the Lakekeeper REST catalog, and the engine dbt builds the marts on
([ADR-035](../../docs/adr/ADR-035-trino-and-dbt-for-marts.md)). Spark keeps everything from
raw to gold. Where Trino may write, and who may, is
[ADR-044](../../docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md):

| Catalog | Lakekeeper warehouse | Files under | Who writes |
|---|---|---|---|
| `lake` | `k2`: `raw`, `bronze`, `silver`, `gold`, `audit` | `s3://k2-lake/warehouse/k2/` | nobody through Trino: `iceberg.security=read_only` |
| one per data domain, named from its id (`market-analytics` is `market_analytics`) | the same name, created by `docker/lake/init-lake.sh` | `s3://k2-lake/warehouse/<name>/` | SQL statements: its writer `dbt_<name>` only; everyone reads. Table procedures: not fenced per user |

Today's domains, from `contracts/domains.yaml`: `fx_reference`, `market_analytics`,
`market_capture`, `reference_data`. Inside a domain catalog, one schema per data product,
named from the product (dbt creates them).

`etc/` is mounted read-only over the image's `/etc/trino`. A changed file needs
`docker compose up -d --force-recreate --no-deps trino` (`scripts/dev-up.sh` does it when
`docker/trino/` changed); Trino reads its configuration only at start.

| File | Kind | What it sets |
|---|---|---|
| `etc/config.properties` | hand-written | one node: coordinator and its only worker; HTTP on 8080 inside the network |
| `etc/jvm.config` | hand-written | heap `-Xms2816M -Xmx2816M` under the 4G limit, JVM time zone `UTC`, exit on `OutOfMemoryError` (no heap dump: nowhere bounded to put one) |
| `etc/node.properties` | hand-written | environment `k2`, data directory |
| `etc/catalog/lake.properties` | hand-written | warehouse `k2`, `iceberg.security=read_only`; static MinIO keys from `${ENV:MINIO_ROOT_USER}` / `${ENV:MINIO_ROOT_PASSWORD}`, path-style, region `local-01` |
| `etc/catalog/<domain>.properties` | **generated** | the domain's warehouse, writable, Lakekeeper-assigned locations, `register_table` and `add_files` off |
| `etc/rules.json` | **generated** | file-based access control, below |
| `etc/access-control.properties` | hand-written | points Trino at `rules.json` |
| `test/local/<domain>.properties` | **generated** | each domain catalog's twin on a local metastore, for `make test-trino` |
| `test/local/lake.properties` | hand-written | the read-only `lake` twin over the same metastore |

Generated files come from `contracts/domains.yaml` through `make generate`
(`platform/k2gen/k2gen/generate.py`), with `docker/lake/generated/warehouses.txt`, the list
`init-lake.sh` creates. Never edit them: add or change a domain, then `make generate`; the CI
drift gate fails a stale one.

**Heap.** 2816M of the 4 GiB limit leaves 1.25 GiB for metaspace, the 256M code cache,
thread stacks and direct buffers. A single query ran at 1 CPU / 2 GiB with a 1280M heap in
the spike; the larger limit is for dbt running several models at once.

**Time zone.** The JVM is pinned to UTC, the session zone of a client that sends none. A
client that sends its own zone gets that zone, so pass `--timezone UTC` to the CLI and set
it in the dbt profile.

## The fences

1. **`lake` is read-only at the connector.** `iceberg.security=read_only` refuses every
   write, DDL and `ALTER TABLE ... EXECUTE` (`optimize`, `rollback_to_snapshot`,
   `expire_snapshots`, `remove_orphan_files` and the rest). File-based rules alone cannot:
   `checkCanExecuteTableProcedure` is an empty method in both file-based access controls of
   `lib/trino-plugin-toolkit` at tag 484 (lines 1043 and 727), and a `rollback_to_snapshot` of
   `raw.messages` would lose offsets.
2. **`rules.json`, per writer.** First match wins in each list. Catalogs `lake` and the domains
   are `all` at the catalog level and `system` read-only; the fence is in the schema and table
   rules: `dbt_<domain>` owns its own catalog's schemas and tables, and every user, that writer
   included, has `SELECT` and `GRANT_SELECT` on everything else. A user no rule names (`trino`,
   `someone`) reads everything and writes nothing with SQL statements. The catalog level stays
   `all` because a definer view is readable by others only if its owner has `all` on each base
   table's catalog: a read-only catalog rule gave `Access Denied: View owner does not have
   sufficient privileges` on 2026-11-25. Three more lists:
   - no `procedures` list, so `CALL` runs only Trino's built-ins: that default is what denies
     `CALL <catalog>.system.rollback_to_snapshot`, `register_table` and the rest in a domain
     catalog. In `lake` the read-only connector would deny them too (by the source; not
     tested on its own);
   - `catalog_session_properties` denies `expire_snapshots_min_retention` and
     `remove_orphan_files_min_retention` to every user, a domain's writer included, and allows
     the rest. Without it anyone could lower the 7-day floor to `0s` and expire a table's
     snapshots (probed on `k2-trino`, 2026-11-25: 2 snapshots became 1);
   - `queries`: every user executes; a user sees and kills only its own queries.

**Identity is asserted, not authenticated.** The user is the `X-Trino-User` header (`trino
--user`); anyone on the host can claim `dbt_market_analytics`. The port is published on
`127.0.0.1` only. The fences stop mistakes, not a liar, and Lakekeeper is unauthenticated too
([ADR-028](../../docs/adr/ADR-028-non-goals-and-honest-limits.md) section 8). The second
layer, a MinIO key per identity scoped to its prefix, comes with the entitlements phase.

**The known gap.** Inside the domain catalogs, table procedures are not fenced per user: no
built-in access control in Trino 484 restricts `ALTER TABLE ... EXECUTE` per user on a
writable catalog. `EXPLAIN ALTER TABLE reference_data.scratch_check.candles EXECUTE optimize`
as `dbt_market_analytics` planned on 2026-11-25. So any user can still run `optimize`,
`rollback_to_snapshot`, and `expire_snapshots` or `remove_orphan_files` at or above the 7-day
floor on any domain's marts. The lake is not exposed by it, and a mart is rebuilt by dbt;
`make test-trino` asserts the gap, so a release that closes it fails there.

## Operating the domain warehouses

**`lake-init` failed on a domain warehouse.** `init-lake.sh` creates them last, after the
lake's namespaces, so the lake itself is bootstrapped; but the one-shot exits 1, and what
waits for it does not start: `lake-ddl` (and so `airflow-scheduler`) and `trino`. Read the
reason, fix it (usually a name Lakekeeper refuses, or Lakekeeper or MinIO not up), then run
the one-shot again and start what waited:

```bash
docker logs k2-lake-init | tail -20
docker compose up -d --force-recreate --no-deps lake-init
docker compose up -d lake-ddl airflow-scheduler trino
```

**Removing a domain.** Delete it from `contracts/domains.yaml` and `make generate`: its
catalog file, its rules and its `warehouses.txt` line go. `init-lake.sh` only creates, so the
Lakekeeper warehouse and every object in it stay, invisible to Trino, and re-adding the same
id would bring the old tables back. To remove the warehouse for good, confirm it is empty,
then delete it (how the earlier single warehouse `marts` went, 2026-11-25: `204`):

```bash
ID=$(curl -s 'localhost:18181/management/v1/warehouse?project-id=00000000-0000-0000-0000-000000000000' \
  | python3 -c 'import json,sys; print([w["id"] for w in json.load(sys.stdin)["warehouses"] if w["name"] == "<name>"][0])')
curl -s "localhost:18181/catalog/v1/$ID/namespaces"          # must print {"namespaces":[]}
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE "localhost:18181/management/v1/warehouse/$ID"   # 204
```

`make test-trino` (CI job `trino`) checks all of this with no stack: the real `etc/` starts
with exactly `lake`, `system` and one catalog per domain, only `lake` is on `k2` and it is
`read_only`; then, on local catalogs from the same generator, each writer creates, inserts
and drops in its own catalog; the next domain's writer and an unnamed user are denied every SQL
write on it (insert, delete, update, add column, view, drop); table procedures and `CALL`
through `lake` are denied, and `CALL` through a domain catalog, its own writer included;
nobody may set the two retention floors, so an expire at `0s` fails;
a view across two catalogs is read by a third user; a user sees only its own queries.

## Verified 2026-11-25

Image `trinodb/trino:484` (release 484, 15 Oct 2026; digest
`sha256:2704eb53935af5e582d63d6033e9bf99d20c325e9b0e30b1016ef59a4773979f`) against
Lakekeeper v0.13.9 and MinIO on the running stack, in throwaway containers on the compose
network (`k2-trino-spike`, then `k2-trino-check` with this directory as committed), and the
closed-path probes against the compose service `k2-trino`:

| Check | Result |
|---|---|
| Namespaces and tables through Lakekeeper, static keys | listed; first lake query 9.7 s after `docker run` at 2 CPU / 4 GiB (20 to 23 s at 1 CPU) |
| Types | `timestamp(6) with time zone`, `decimal(38,10)`, `map(varchar, varbinary)`, `array(row(...))` all map |
| `FOR VERSION AS OF <snapshot id>` on `lake.gold.ohlcv_1m` | works through the read-only catalog |
| `lake`: `EXPLAIN ALTER TABLE ... EXECUTE optimize`, `ALTER TABLE lake.gold.ohlcv_1m EXECUTE rollback_to_snapshot(<current main>)` | `Access Denied: Cannot execute table procedure OPTIMIZE` / `ROLLBACK_TO_SNAPSHOT on gold.ohlcv_1m`; `main` unchanged |
| `lake`: `CREATE TABLE`, `INSERT`, `DELETE`, `CREATE SCHEMA`, `CALL lake.system.unregister_table` | `Access Denied` for each |
| Domain warehouses through `init-lake.sh` | `fx_reference`, `market_analytics`, `market_capture`, `reference_data` created (`201`), then `exists, skipping create` on a re-run; the earlier single warehouse `marts`, with `{"namespaces":[]}`, deleted (`DELETE /management/v1/warehouse/<id>`: `204`) |
| Each writer in its own catalog | `dbt_<domain>`: `CREATE SCHEMA <domain>.scratch_check`, a 2,000-row CTAS from `lake.gold.ohlcv_1m`; files under `s3://k2-lake/warehouse/<domain>/<uuid>` |
| A writer in another domain | `dbt_market_analytics`: `Access Denied: Cannot create table reference_data.scratch_check.intruder`; insert, drop, `CREATE SCHEMA` and `CREATE VIEW` there denied likewise |
| A user no rule names | `Access Denied: Cannot create table market_analytics.scratch_check.x` |
| Reads across fences | one statement joining `market_analytics`, `reference_data` and `lake` read back; a view in `market_analytics` over `reference_data` and `lake.gold.ohlcv_1m` with a 20-minute delay, stored under `warehouse/market_analytics`, read by another user |
| Escapes, probed on `k2-trino` | a table at `location='s3://k2-lake/warehouse/k2/_escape_test'`: `Failed to create transaction` (Lakekeeper refused; nothing created); `CALL ... register_table` and `unregister_table`: `Access Denied: Cannot execute procedure`; `add_files`: `add_files procedure is disabled` |
| `DROP TABLE`, `DROP VIEW`, `DROP SCHEMA` | a purge drop: Lakekeeper's `tabular_purge` task deleted the files; 0 objects under every domain prefix within seconds |
| Warehouse `k2` afterwards | namespaces unchanged, 0 Trino-written snapshots in its 26 tables, the ingest committing every 5 minutes throughout |
| Snapshot summary of a Trino write | `engine-name`, `engine-version`, `trino_query_id`, `trino_user`; no `k2.*` property |
| Session time zone | `UTC` from a client that sends none, also with a non-UTC zone (for example `TZ=America/New_York`) set in the container |
| Idle, 2 CPU / 4 GiB | 1.42 GiB, about 3% CPU |
| The candle aggregation (open, high, low, close, volume, count) over all of `gold.trades`, read only | 6.6 s alone; four at once 12.5 s each at most, peak 2.86 GiB of 4 GiB and 2.15 cores, no OOM |

`gold.candles()` ported verbatim fails with `Decimal overflow`: Trino types
`DECIMAL(38,10) * DECIMAL(38,10)` as `decimal(38,6)` but overflows once the exact product
passes about 1e18. A `DECIMAL(38,0)` product of the two `_e8` integers runs.

Proof commands for the live service, with nothing written:

```bash
docker exec k2-trino trino --execute 'EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize'
#   ... failed: Access Denied: Cannot execute table procedure OPTIMIZE on gold.ohlcv_1m
docker exec k2-trino trino --user dbt_market_analytics --execute 'CREATE TABLE reference_data.s.t (x integer)'
#   ... failed: Access Denied: Cannot create table reference_data.s.t   (ran against k2-trino, 2026-11-25)
```
