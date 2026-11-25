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
| Does a read-only connector? | Yes, for statements and table procedures: with `iceberg.security=read_only`, `ALTER TABLE lake.gold.ohlcv_1m EXECUTE rollback_to_snapshot(<current main>)` gave `Access Denied: Cannot execute table procedure ROLLBACK_TO_SNAPSHOT on gold.ohlcv_1m`; `optimize`, `CREATE TABLE`, `INSERT`, `DELETE` and `CREATE SCHEMA` were denied; reads and time travel work |
| Who denies `CALL`? | The file rules' default first: with no `procedures` list only Trino's built-in procedures run (`FileBasedSystemAccessControl.checkCanExecuteProcedure`, line 1016), so `CALL lake.system.rollback_to_snapshot('gold','ohlcv_1m',<current main>)` as an unnamed user gave `Access Denied: Cannot execute procedure lake.system.rollback_to_snapshot`, and `CALL <domain>.system.*` is denied the same way. For `lake` the read-only connector would deny it too, by the source: `ReadOnlyAccessControl` does not override `checkCanExecuteProcedure`, whose interface default denies; that was not separately tested. For a domain catalog the default rule is the only layer |
| Can a user lower the retention floors? | Yes, until this change: with no `catalog_session_properties` list every user may set every session property, and `<catalog>.expire_snapshots_min_retention` and `remove_orphan_files_min_retention` have no lower bound. As an unnamed user, `--session market_analytics.expire_snapshots_min_retention=0s` then `EXPIRE_SNAPSHOTS` at `0s` ran on a scratch table and its snapshots went from 2 to 1; without the session property the same statement failed with `Retention specified (0.00s) is shorter than the minimum retention configured in the system (7.00d)` (probed on `k2-trino`). Fixed in the generated rules: both properties denied to every user in every catalog, the rest allowed |
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
`k2gen validate` refuses a domain whose name would be `lake`, `system`, `k2`,
`information_schema` or a Trino reserved word (`table`, `select`, `schema`, `catalog`,
`default`, `user`). Inside a
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

| Identity, SQL statements | Own domain catalog | Other domain catalogs | `lake` |
|---|---|---|---|
| `dbt_<domain>` | create and drop schemas, tables, views; insert, delete, update | read | read |
| any other user, `trino` and unnamed ones included | read | read | read |
| nobody | lower a retention floor | lower a retention floor | write: the connector refuses every statement that would commit, and every table procedure |

The table covers SQL statements. Catalog rules are `all` for `lake` and the domains, and the
fence sits in the schema and table rules, because a definer view must be readable across
domains (the last row of the evidence). `CALL` runs only Trino's built-in procedures, by the
default of an absent `procedures` list. The two retention-floor session properties are denied
to everyone, a domain's writer in its own catalog included: snapshot maintenance belongs to the
platform, not to a SQL user. A `queries` rule lets every user execute and see or kill only its
own queries.

**A mart that joins two domains is owned by, and stored in, the consuming domain.** Products
depend on products; nobody writes into a producer's warehouse. This follows from the layout:
the only warehouse a domain can write is its own.

Consequences of the layout, not separate decisions:

- Marts are reached through Trino, where the fences are; ADR-035 records Trino as the front
  door for marts. A reader on another engine attaches each warehouse it needs.
- A schema per product inside a domain catalog (above).
- **Two departures from the phase file's wording**, recorded rather than edited into the dated
  plan: the marts live in per-domain warehouses, not a namespace `marts` of the lake; and the
  planned `governance.dq_results` view lands in a domain catalog, because every object Trino
  owns, views included, lives in one.

**Governance data, as intent only.** The maintainer's direction of 2026-11-25 is that quality
results, control evidence and lineage become a platform-owned domain stored by this same
pattern. It is decided in its own ADR when its first product lands, because it reverses
ADR-033's line that the platform owns the road, not the products.

**Removing a domain.** `make generate` then drops its catalog file, its rules and its
`warehouses.txt` line, but `init-lake.sh` only creates: the Lakekeeper warehouse and its objects
stay, invisible to Trino, and re-adding the same id would bring the old tables back. To remove one
for good: confirm the warehouse is empty (`GET /catalog/v1/<warehouse id>/namespaces` returns
`{"namespaces":[]}`), then `DELETE /management/v1/warehouse/<warehouse id>`; that is how the
single warehouse `marts` of this pull request's earlier draft was removed (`204`).

**The honest limits.** Identity is the unauthenticated `X-Trino-User` header on a loopback
port: the fences stop mistakes, not a liar. The storage-level second layer, a MinIO key per
identity scoped to its own prefix, comes with the entitlements phase (P5, ADR-039 as reserved in
[ADR-031](ADR-031-governed-self-serve-platform.md)); today Trino holds the root keys. And
**table procedures inside the domain catalogs are not fenced per user**: no built-in access
control in Trino 484 restricts `ALTER TABLE ... EXECUTE` per user on a writable catalog, so any
user can still run `optimize`, `rollback_to_snapshot`, and `expire_snapshots` or
`remove_orphan_files` at or above their 7-day floor on any domain's marts. `make test-trino`
asserts the gap so a release that closes it is noticed. The lake is not exposed by it; a mart is
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
domains in both directions (ADR-033's trigger); the first governance product lands (its own
ADR).

---

## References

- [ADR-033](ADR-033-data-mesh-four-domains.md), extended here; its appended Outcome records
  the physical mart warehouses and the pending governance decision. [ADR-035](ADR-035-trino-and-dbt-for-marts.md), the engine.
  [ADR-045](ADR-045-ceiling-25-cpu-48-gib.md), the budget.
- Code: `platform/k2gen/k2gen/generate.py`, `docker/trino/`, `docker/lake/init-lake.sh`,
  `scripts/trino-rules-test.sh`
- Upstream: Trino 484 file-based and read-only access control, `lib/trino-plugin-toolkit` at
  tag 484; the Iceberg connector's `iceberg.security`

---

## Outcome (2026-11-25, the first governance product)

The trigger "the first governance product lands" fired, and
[ADR-046](ADR-046-governance-domain-and-quality-results.md) is the decision: domain `governance`
gets its warehouse, catalog and writer from `make generate` with nothing in this layout changed,
and the planned `governance.dq_results` view is `governance.quality_results.dq_results`, in its
product's schema. One addition to "one schema per data product": every domain catalog also has
a schema `dq`, where its own writer records the domain's dbt test results; like `staging`, it is
the platform's, not a product's, and `k2gen validate` refuses a product named after it.

Correction, 2026-11-26: the Decision's example mart `market_analytics.delayed_candles.ohlcv_1m_delayed_20m`
was built as `market_analytics.delayed_candles.ohlcv_delayed_20m`
(`contracts/market-analytics/delayed-candles/product.yaml`).
