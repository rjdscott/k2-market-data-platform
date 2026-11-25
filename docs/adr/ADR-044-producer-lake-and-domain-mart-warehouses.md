# ADR-044: One producer lake, one mart warehouse per domain, fenced per writer

**Status:** Accepted
**Date:** 2026-11-25
**Author:** Rob Scott
**Category:** Storage layout, Access

---

## Context

Trino is the engine for marts ([ADR-035](ADR-035-trino-and-dbt-for-marts.md)). Where it may
write, and who may write what, is a separate question, and the lake makes it a sharp one: Spark
writes `raw` to `gold` in warehouse `k2`, the ingest resumes from the newest ingest snapshot on
any ref ([ADR-022](ADR-022-exactly-once-via-snapshot-offsets.md), `docker/lake/offsets.py`),
and so a single stray commit, a `rollback_to_snapshot` above all, would skip offsets for good.
Products are owned per domain ([ADR-033](ADR-033-data-mesh-four-domains.md)), so a consumer
mart has an owner before it has a table.

This number falls outside the block ADR-031 reserved (032 to 042; 043 is the venue reference
bars plan's) because the umbrella did not foresee the decision: the phase file puts the marts
in one namespace `marts` of the lake.

Measured on the running stack, 2026-11-25, Trino 484 against Lakekeeper 0.13.9
([`docker/trino/README.md`](../../docker/trino/README.md)):

| Question | Measured |
|---|---|
| Can file-based rules fence a writable catalog? | Not against table procedures. `checkCanExecuteTableProcedure` is an empty method in `FileBasedSystemAccessControl` (line 1043) and `FileBasedAccessControl` (line 727) at tag 484; through a writable catalog over `k2`, `EXPLAIN ALTER TABLE lake.gold.ohlcv_1m EXECUTE optimize` planned while `EXPLAIN INSERT` into it was denied |
| Does a read-only connector? | Yes: with `iceberg.security=read_only`, `ALTER TABLE lake.gold.ohlcv_1m EXECUTE rollback_to_snapshot(<current main>)` gave `Access Denied: Cannot execute table procedure ROLLBACK_TO_SNAPSHOT on gold.ohlcv_1m`; `optimize`, `CREATE TABLE`, `INSERT`, `DELETE`, `CREATE SCHEMA` and `CALL lake.system.unregister_table` were denied; reads and time travel work |
| `DROP TABLE` | a purge drop: Lakekeeper's `tabular_purge` task deletes the files within seconds |
| Escapes from a writable catalog (probed on `k2-trino`) | a table at `location='s3://k2-lake/warehouse/k2/...'`: `Failed to create transaction`, refused by Lakekeeper; `CALL ... register_table` / `unregister_table`: `Access Denied: Cannot execute procedure`; `add_files`: `add_files procedure is disabled` |
| Per-domain warehouses and writers, live (`k2-trino-check`, this configuration) | each writer `dbt_<domain>` created a schema and a 2,000-row CTAS from `lake.gold.ohlcv_1m` in its own catalog, under `s3://k2-lake/warehouse/<domain>/<uuid>`; `dbt_market_analytics` into `reference_data`: `Access Denied: Cannot create table reference_data.scratch_check.intruder` (insert, drop, schema and view likewise); a user no rule names: `Access Denied: Cannot create table market_analytics.scratch_check.x`; one statement joining two domain catalogs and `lake` read back; a view in `market_analytics` over `reference_data` and `lake.gold.ohlcv_1m`, stored under `warehouse/market_analytics`, read by another user. Dropped after: 0 objects under every domain prefix, 0 Trino-written snapshots in `k2`, its namespaces unchanged |
| A read-only catalog rule for other catalogs | breaks definer views: `Access Denied: View owner does not have sufficient privileges: Cannot select from table lake.gold.ohlcv_1m`; Trino requires the view owner to have `all` on each base table's catalog (`checkCanCreateViewWithSelectFromColumns`) |

---

## Decision

**We will keep one producer lake, warehouse `k2`, which Spark alone writes and Trino reads
through a catalog that is read-only at the connector, and give every data domain in
`contracts/domains.yaml` a consumer warehouse of its own for its marts, with a Trino catalog
and a writer identity generated from that file, because the fence a reviewer can trust is the
connector's, ownership is per domain everywhere, and the consumer side is where domains
genuinely differ.** Scope: where marts live and who may write them. The lake's tables do not
move.

**Names.** Domain `market-analytics` gets warehouse and Trino catalog `market_analytics`, key
prefix `warehouse/market_analytics` in bucket `k2-lake`, and writer `dbt_market_analytics`.
`k2gen validate` refuses a domain whose name would be `lake`, `system` or `k2`. Inside a
domain catalog, **one schema per data product**, named from the product slug
(for example `market_analytics.delayed_candles.ohlcv_1m_delayed_20m`): the product is the unit of
ownership, contract and lifecycle, so a schema per product lets the entitlements phase grant,
and a product rebuild drop, one product without touching its siblings, and dbt maps one model
folder per product to one schema.

**Generated, not hand-kept.** `make generate` writes from `contracts/domains.yaml`
(`platform/k2gen/k2gen/generate.py`): `docker/lake/generated/warehouses.txt`, which
`docker/lake/init-lake.sh` creates after the lake's own bootstrap;
`docker/trino/etc/catalog/<domain>.properties`, writable, Lakekeeper-assigned locations,
`register_table` and `add_files` pinned off; its local-metastore twin under
`docker/trino/test/local/`; and `docker/trino/etc/rules.json`. Adding a domain and running
`make generate` is the whole act of giving it a fence; the CI drift gate fails a stale one.
`lake.properties` stays hand-written.

**What each identity can do** (`rules.json`, file-based, first match wins):

| Identity | Own domain catalog | Other domain catalogs | `lake` |
|---|---|---|---|
| `dbt_<domain>` | create and drop schemas, tables, views; insert, delete, update | read | read |
| any other user, `trino` and unnamed ones included | read | read | read |
| nobody | | | write: the connector refuses every commit, table procedures included |

Catalog rules are `all` for `lake` and the domains, and the fence sits in the schema and table
rules, because a definer view must be readable across domains (the last row of the evidence).
`CALL` runs only Trino's built-in procedures: there are no procedure rules.

**Further decisions recorded here:**

- **A mart that joins two domains is owned by, and stored in, the consuming domain.**
  Products depend on products; nobody writes into a producer's warehouse.
- **Governance data is a domain like any other**, owned by the platform team: quality
  results, control evidence and lineage are stored by the same pattern. It is added to
  `domains.yaml` with its first product, not now: the registry's `kind` is source- or
  consumer-aligned, and an empty declaration would have to pick one before there is a product
  to decide it.
- **Trino is the front door for marts**: consumers read marts through Trino only, which is
  what lets the delayed product be a view (ADR-035).
- **Two departures from the phase file's wording**, recorded rather than edited into the dated
  plan: the marts live in per-domain warehouses, not a namespace `marts` of the lake; and the
  planned `governance.dq_results` view lands in the governance domain's catalog, because every
  object Trino owns, views included, lives in a domain catalog.

**The honest limits.** Identity is the unauthenticated `X-Trino-User` header on a loopback
port: the fences stop mistakes, not a liar. The storage-level second layer, a MinIO key per
identity scoped to its own prefix, comes with the entitlements phase (P5, ADR-039 as reserved in
[ADR-031](ADR-031-governed-self-serve-platform.md)); today Trino holds the root keys. And
**table procedures inside the domain catalogs are not fenced per user**: no built-in access
control in Trino 484 restricts `ALTER TABLE ... EXECUTE` per user on a writable catalog, so any
user can run `optimize`, `rollback_to_snapshot`, or `expire_snapshots` and
`remove_orphan_files` above their 7-day floor on any domain's marts. `make test-trino` asserts
the gap so a release that closes it is noticed. The lake is not exposed by it; a mart is
rebuilt by dbt from the lake.

---

## Alternatives Considered

| Option | Why not |
|--------|---------|
| One warehouse, marts as a namespace of `k2`, a writable catalog fenced by file-based rules (the phase file's shape) | the empty `checkCanExecuteTableProcedure`: `rollback_to_snapshot` on `raw.messages` would pass and lose offsets |
| One shared `marts` warehouse, domains logical only (this pull request's previous draft) | protects the lake, but no domain is fenced from another, and the fence a reviewer can see on the stack is one, not one per owner |
| Per-domain warehouses for the lake too | every existing table moves, the ingest writes across warehouses, the parity pins and audits are re-based, for fences nobody stands behind: the lake's layers are built by one pipeline and one team |
| Read-only catalog rules for other domains | measured to break definer views across domains |
| Two Trino catalogs per domain (a read-only one for readers, a writable one for its writer) | doubles every name, and by the source cannot stop a table procedure either: `AccessControlManager.checkCanExecuteTableProcedure` checks no catalog access, and the file-based check it calls is empty |
| Trino's `opa` access control with an OPA server | governs table procedures per user, and so closes the gap; a new service and a policy language, which belong with the entitlements phase |

---

## Consequences

**Easier:** a reviewer sees one fence per domain on the running stack and a denied
cross-domain write; a new domain gets its fence from one line in `domains.yaml`; a domain's
files are separable by prefix.

**Harder:** every engine that needs a domain's marts is configured per warehouse; views are
Trino-only; four empty warehouses exist before their first marts. The next pull request
teaches the readers and maintainers: `docker/lake/spark_conf.py` and `maintenance.py`,
`docker/lake/metrics.py`, `notebooks/k2lake.py` and the DuckDB `ATTACH` in the parity scripts,
and `k2gen`'s contract checks. Each can read `docker/lake/generated/warehouses.txt` rather than
carry its own list.

**Committed to:** `contracts/domains.yaml` as the source of every fence; `iceberg.security=read_only`
on `lake`; the identity names above; `make test-trino` and the drift gate in CI.

**Revisit when:** a Trino release governs `checkCanExecuteTableProcedure` in file-based access
control, or the entitlements phase brings OPA or catalog-level authorization (then close the
table-procedure gap, and `make test-trino`'s known-gap check fails first); a second person gets
access to the host (authentication on Trino before anything else); a product's inputs cross
domains in both directions (ADR-033's trigger); the first governance product lands (add the
domain).

---

## References

- [ADR-033](ADR-033-data-mesh-four-domains.md), extended here; its appended Outcome records
  the governance domain. [ADR-035](ADR-035-trino-and-dbt-for-marts.md), the engine.
  [ADR-045](ADR-045-ceiling-25-cpu-48-gib.md), the budget.
- Code: `platform/k2gen/k2gen/generate.py`, `docker/trino/`, `docker/lake/init-lake.sh`,
  `scripts/trino-rules-test.sh`
- Upstream: Trino 484 file-based and read-only access control, `lib/trino-plugin-toolkit` at
  tag 484; the Iceberg connector's `iceberg.security`
