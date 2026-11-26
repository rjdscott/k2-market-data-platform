# docker/trino

One Trino node, the `trino` service in `docker-compose.yml`: SQL over the Iceberg lake
through the Lakekeeper REST catalog, and the engine dbt builds on
([ADR-035](../../docs/adr/ADR-035-trino-and-dbt-for-marts.md)). The old pipeline that filled the
lake, Spark from raw to gold and the dbt marts, is retired to
[`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/) and its tables dropped: the lake holds
no crypto table until phase `004`, and dbt builds again from phase `002`. Where Trino may write,
and who may, is
[ADR-044](../../docs/adr/ADR-044-producer-lake-and-domain-mart-warehouses.md):

| Catalog | Lakekeeper warehouse | Files under | Who writes |
|---|---|---|---|
| `lake` | `k2`, empty since its old namespaces were dropped | `s3://k2-lake/warehouse/k2/` | nobody through Trino: `iceberg.security=read_only` |
| two per data domain, named from its id (`market-analytics` is `market_analytics`, read-only, and `market_analytics_build`, writable) | the same name, created by `docker/lake/init-lake.sh` from the release's storage plan | `s3://k2-lake/warehouse/<name>/` | only the principals the release's compiled rules name ([ADR-059](../../docs/adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md)); readers see a dbt output only through a guarded view ([ADR-061](../../docs/adr/ADR-061-readers-see-dbt-outputs-through-a-view-over-the-tested-tag.md)) |

Today's domains, from `contracts/domains.yaml`: `fx_reference`, `governance`,
`market_analytics`, `market_capture`, `reference_data`. Inside a domain catalog, one schema per
data product, named from the product, a `staging` schema of views, and `dq`, where the domain's
own writer records its dbt test results; dbt creates them ([`dbt/README.md`](../../dbt/README.md),
[ADR-046](../../docs/adr/ADR-046-governance-domain-and-quality-results.md)).

**Where the etc comes from.** Not from this directory: `docker/trino/etc` was deleted on
2026-11-27. k2gen compiles the whole etc (`platform/k2gen/k2gen/emit/trino_etc.py`) into each
release's `compiled/trino/etc/`, and the `trino` service starts with
`--etc-dir=/k2/releases/pointers/trino/compiled/trino/etc`, through the release pointer
([ADR-058](../../docs/adr/ADR-058-deploy-sealed-releases-by-pointer-swap.md)). It holds
`config.properties` (HTTPS only on 8443, password logins, Web UI off), `jvm.config`,
`node.properties`, `log.properties`, `catalog/` (`lake` and the two per domain),
`access-control.properties` (the release's `rules.json`, through the pointer, reread every
5 s), the group provider and the resource groups. A release that changes only the rules is
picked up without a restart; a change under `etc` restarts Trino (`make deploy` plans it).
Never edit a compiled file: change the documents, tag, `make release`, `make deploy`.

**Heap.** 2816M of the 5 GiB limit leaves 2.25 GiB for metaspace, the 256M code cache,
thread stacks and direct buffers; nothing here caps direct memory. A single query ran at 1 CPU /
2 GiB with a 1280M heap in the spike; the larger limit is for dbt running several models at
once. The limit was 4 GiB until 2026-11-26, when the dbt marts' first builds left the cgroup's
`anon` memory 0.2 GiB under it (`docker exec k2-trino cat /sys/fs/cgroup/memory.stat`;
[ADR-035](../../docs/adr/ADR-035-trino-and-dbt-for-marts.md) Outcome).

**Time zone.** The JVM is pinned to UTC, the session zone of a client that sends none. A
client that sends its own zone gets that zone, so pass `--timezone UTC` to the CLI and set
it in the dbt profile.

## The fences

1. **`lake` is read-only at the connector.** `iceberg.security=read_only` refuses every
   write, DDL and `ALTER TABLE ... EXECUTE` (`optimize`, `rollback_to_snapshot`,
   `expire_snapshots`, `remove_orphan_files` and the rest). File-based rules alone cannot:
   `checkCanExecuteTableProcedure` is an empty method in both file-based access controls of
   `lib/trino-plugin-toolkit` at tag 484 (lines 1043 and 727), and a `rollback_to_snapshot` of
   a table whose snapshots carry its Kafka offsets (the retired raw archive did) would lose them.
2. **The rules, compiled default-deny for each release** from `contracts/access/` and each
   product's grants ([ADR-059](../../docs/adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md)).
   The hand-written `rules.json` this section described until 2026-11-27 went with
   `docker/trino/etc`.

**Identity is a password login over HTTPS** under a local CA
([ADR-060](../../docs/adr/ADR-060-trino-logins-by-password-over-tls-under-a-local-ca.md); `make tls`,
`make password PRINCIPAL=<id>`, secrets under `${K2_SECRETS}`, never the repository). Until
2026-11-27 the user was the unchecked `X-Trino-User` header. Lakekeeper is still unauthenticated
and is fenced by network instead (ADR-059 part 7). Trino's S3 keys are `K2_TRINO_S3_*`, MinIO's
root pair until phase 002 gives it a scoped key.

**The startup log and the S3 keys.** Trino logs every catalog property at start, one INFO
line each under `io.trino.bootstrap.catalog.<catalog>`, and masks only what the connector
marks security-sensitive: `s3.aws-secret-key` is masked, `s3.aws-access-key` is not. Until
2026-11-26 that put the MinIO access key in `docker logs k2-trino` once per catalog per
start. The compiled `log.properties` sets that logger to `WARN` (its golden file pins the line);
the check that started the old `etc/` with a canary key and read the log went with it. `SHOW CREATE CATALOG` does not exist
in Trino 484 and `system.metadata.catalogs` lists no properties (probed 2026-11-26), so SQL
does not reveal the keys either.

**The known gap.** Inside the domain catalogs, table procedures are not fenced per user: no
built-in access control in Trino 484 restricts `ALTER TABLE ... EXECUTE` per user on a
writable catalog. `EXPLAIN ALTER TABLE reference_data.scratch_check.candles EXECUTE optimize`
as `dbt_market_analytics` planned on 2026-11-25. So any user can still run `optimize`,
`rollback_to_snapshot`, and `expire_snapshots` or `remove_orphan_files` at or above the 7-day
floor on any domain's marts. The lake is not exposed by it, and a mart is rebuilt by dbt.
This was measured on the old hand-written rules, 2026-11-25.
<!-- VERIFY: whether the compiled rules of ADR-059 still leave this gap, and which make test-trino row shows it -->

## Operating the domain warehouses

**`lake-init` failed on a domain warehouse.** `make deploy` runs the one-shots before it moves
anything, and `init-lake.sh` creates the domain warehouses last, after the bucket and the `k2`
warehouse. Read the reason (`docker logs k2-lake-init | tail -20`), fix it (usually a name
Lakekeeper refuses, or Lakekeeper or MinIO not up), then deploy again:
[`docs/runbooks/deploy-a-release.md`](../../docs/runbooks/deploy-a-release.md).

**Removing a domain.** Delete it from `contracts/domains.yaml`, tag, `make release`,
`make deploy`: its catalogs and rules go with the next release. `init-lake.sh` only creates, so
the Lakekeeper warehouse and every object in it stay, invisible to Trino, and re-adding the same
id would bring the old tables back. On 2026-11-25 the earlier single warehouse `marts` was
confirmed empty and deleted through Lakekeeper's management API on its host port (`204`); that
port is gone, so the API is reached only from a container on `k2-catalog`.
<!-- VERIFY: the warehouse delete from a container on k2-catalog, then write the commands as run -->

`make test-trino` (CI job `trino`) checks the rules with no stack: it compiles a release from a
test document set, starts that release's etc and rules on a throwaway Lakekeeper, Postgres,
MinIO and Trino 484, logs every user in by password over HTTPS, and holds each role to exactly
its policy's rows and masks; one reader rule widened in place must then fail its rows
(`scripts/trino-rules-test.sh`).

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
| `FOR VERSION AS OF <snapshot id>` on `lake.gold.ohlcv_1m` (since retired) | works through the read-only catalog |
| `lake`: `EXPLAIN ALTER TABLE ... EXECUTE optimize`, `ALTER TABLE lake.gold.ohlcv_1m EXECUTE rollback_to_snapshot(<current main>)`, on the since retired table | `Access Denied: Cannot execute table procedure OPTIMIZE` / `ROLLBACK_TO_SNAPSHOT on gold.ohlcv_1m`; `main` unchanged |
| `lake`: `CREATE TABLE`, `INSERT`, `DELETE`, `CREATE SCHEMA`, `CALL lake.system.unregister_table` | `Access Denied` for each |
| Domain warehouses through `init-lake.sh` | `fx_reference`, `market_analytics`, `market_capture`, `reference_data` created (`201`), then `exists, skipping create` on a re-run; the earlier single warehouse `marts`, with `{"namespaces":[]}`, deleted (`DELETE /management/v1/warehouse/<id>`: `204`) |
| Each writer in its own catalog | `dbt_<domain>`: `CREATE SCHEMA <domain>.scratch_check`, a 2,000-row CTAS from `lake.gold.ohlcv_1m` (since retired); files under `s3://k2-lake/warehouse/<domain>/<uuid>` |
| A writer in another domain | `dbt_market_analytics`: `Access Denied: Cannot create table reference_data.scratch_check.intruder`; insert, drop, `CREATE SCHEMA` and `CREATE VIEW` there denied likewise |
| A user no rule names | `Access Denied: Cannot create table market_analytics.scratch_check.x` |
| Reads across fences | one statement joining `market_analytics`, `reference_data` and `lake` read back; a view in `market_analytics` over `reference_data` and `lake.gold.ohlcv_1m` (since retired) with a 20-minute delay, stored under `warehouse/market_analytics`, read by another user |
| Escapes, probed on `k2-trino` | a table at `location='s3://k2-lake/warehouse/k2/_escape_test'`: `Failed to create transaction` (Lakekeeper refused; nothing created); `CALL ... register_table` and `unregister_table`: `Access Denied: Cannot execute procedure`; `add_files`: `add_files procedure is disabled` |
| `DROP TABLE`, `DROP VIEW`, `DROP SCHEMA` | a purge drop: Lakekeeper's `tabular_purge` task deleted the files; 0 objects under every domain prefix within seconds |
| Warehouse `k2` afterwards | namespaces unchanged, 0 Trino-written snapshots in its 26 tables, the ingest committing every 5 minutes throughout |
| Snapshot summary of a Trino write | `engine-name`, `engine-version`, `trino_query_id`, `trino_user`; no `k2.*` property |
| Session time zone | `UTC` from a client that sends none, also with a non-UTC zone (for example `TZ=America/New_York`) set in the container |
| Idle, 2 CPU / 4 GiB | 1.42 GiB, about 3% CPU |
| The candle aggregation (open, high, low, close, volume, count) over all of `gold.trades` (since retired), read only | 6.6 s alone; four at once 12.5 s each at most, peak 2.86 GiB of 4 GiB and 2.15 cores, no OOM |

The notebook helper `gold.candles()` (archived) ported verbatim fails with `Decimal overflow`: Trino types
`DECIMAL(38,10) * DECIMAL(38,10)` as `decimal(38,6)` but overflows once the exact product
passes about 1e18. A `DECIMAL(38,0)` product of the two `_e8` integers runs.

Proof commands for the live service as it was on 2026-11-25, before password logins, with
nothing written. They no longer log in: every login now needs a password over HTTPS.

```bash
docker exec k2-trino trino --user dbt_market_analytics --execute 'CREATE TABLE reference_data.s.t (x integer)'
#   ... failed: Access Denied: Cannot create table reference_data.s.t   (ran against k2-trino, 2026-11-25)
```
