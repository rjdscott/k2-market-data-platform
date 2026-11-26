# 18. Data mesh: domains, products and contracts

> **You will learn** how the lake's tables are grouped into owned data products, what a contract declares, and what `k2gen` checks and writes.
> **Read this if** you own a product, are adding one, or are reviewing a change under `contracts/`.
> **Before this** chapters 09, 13, 14.

## Problem

Chapters 09 to 14 describe the lake by layer: what raw, bronze, silver and gold hold, their
columns, their partitions. That answers how a table is built. It does not answer who owns it,
what it reads, how it is classified, or how long its snapshots live, and before phase P1 of
the [governed self-serve platform plan](../plans/2026-11-24-governed-self-serve-platform/README.md)
nothing in the repository did. The facts that did exist lived in several places that could
disagree: columns and partitioning in `lake.sql`, retention as one CLI default in
`maintenance.py`, schedules in the orchestrator's deployment script, descriptions in the hand-kept
[data catalog](../operations/data-catalog.md).

## Options

| Option | Why it lost | Reference |
|---|---|---|
| Group by layer: raw, bronze, silver, gold | A storage convention, not an owner. The security master and the candles are both gold and share nothing else | [ADR-033](../adr/ADR-033-data-mesh-four-domains.md) |
| A K2-only descriptor format | A second schema to design that nobody else's tooling reads; ODCS v3 has a published JSON Schema and an extension point | [ADR-032](../adr/ADR-032-odcs-contracts-and-k2gen.md) |
| Generate `lake.sql` from the contracts | Loses the reasoning its comments carry and rewrites the DDL of record for no new capability | [ADR-032](../adr/ADR-032-odcs-contracts-and-k2gen.md) |

## Domains and products

Five domains, each owned by one named team ([ADR-033](../adr/ADR-033-data-mesh-four-domains.md);
the fifth, `governance`, [ADR-046](../adr/ADR-046-governance-domain-and-quality-results.md)),
registered in [`contracts/domains.yaml`](../../contracts/domains.yaml) and routed for review
by `.github/CODEOWNERS`. Every team maps to the repository's one account today, so the
boundaries are declared and checked, not yet exercised by a second team.

A **product** is a directory, `contracts/<domain>/<product>/`: a descriptor, `product.yaml`
(owner, purpose, kind, lifecycle, inputs, outputs, known consumers), and one Open Data
Contract Standard v3 contract per output table beside it. `k2gen validate` reports 13
products and 26 contracts: one for every table in `lake.sql`, `audit.checks` included. A product may build only marts, as `delayed-candles`,
`venue-quality` and `quality-results` do, with `outputs: []`.

```mermaid
flowchart TB
  topics["Redpanda: market.crypto.v3 topics"]
  subgraph capture ["market-capture, source-aligned"]
    raw["raw-archive"] --> frames["venue-frames"]
    frames --> vtrades["venue-trades"]
    frames --> vbooks["venue-books"]
  end
  subgraph reference ["reference-data, source-aligned"]
    master["security-master"]
  end
  subgraph analytics ["market-analytics, consumer-aligned"]
    canonical["canonical-trades"] --> candles["candles"]
    canonical --> bars["event-bars"]
    candles --> delayed["delayed-candles"]
    canonical --> vq["venue-quality"]
    snapshots["book-snapshots"]
  end
  subgraph governance ["governance, consumer-aligned"]
    quality["quality-results"]
  end
  audit["lake-audit, market-capture"]
  topics --> raw
  frames --> master
  vtrades --> canonical
  vbooks --> snapshots
  audit --> vq
  audit --> quality
  analytics -. "dbt test results" .-> quality
```

Product to product, from each descriptor's `inputs`. The two configuration files a product
declares as external inputs (`config/instruments.yaml`, `config/bars.yaml`) are left off.
`fx-reference` is declared with its owner and has no product until phase P4. The dotted edge
is not a contract input: every domain's dbt runs record their tests in their own catalog, and
`quality-results` reads those tables as platform plumbing (below). The generated
[product index](../products/README.md) has a page per product, with the same flow drawn table
by table.

## The governance domain

`governance` is a domain like any other, owned by `platform-team`
([ADR-046](../adr/ADR-046-governance-domain-and-quality-results.md)). The platform team owns the
road, and this one domain: its products are records about the other domains' data, which no
domain reads back. Its first product, `quality-results`, is one view,
`governance.quality_results.dq_results`: the lake jobs' checks from `lake.audit.checks` and
every test each domain's dbt runs recorded in its own `dq.dbt_test_results`, with status pass,
fail, warn or error. No writer writes outside its own catalog for it: each domain records its
own results, and the view only reads ([`dbt/`](../../dbt/README.md)). `lake-audit` stays a
`market-capture` product, because the lake's jobs write it about that domain's tables.

## What a contract declares

Each contract places K2's fields where the standard has a place for them, and the rest in
`customProperties`, validated by `contracts/schema/k2.schema.json`. The field map is
[`contracts/schema/README.md`](../../contracts/schema/README.md). Two forms:

- **A table `lake.sql` creates** references it (`authoritativeDefinitions`, url
  `docker/lake/ddl/lake.sql`) and does not restate its columns. `lake.sql` stays the DDL of
  record: its comments carry reasons no generated file would keep.
- **A new table** carries its columns inline (`schema[0].properties`, each with a Spark SQL
  `physicalType` and a description), and `k2gen generate` writes its DDL. No contract has this
  form today; `make new-product` scaffolds one.

`retention` is snapshot expiry in days, not deletion of data: no row in the lake is deleted
([ADR-028](../adr/ADR-028-non-goals-and-honest-limits.md) §12 stays in force).

## What `k2gen validate` checks

In order. A problem in step 1 ends the run, because the later steps assume well-formed files;
steps 2 to 4 report together.

1. Every file against its schema: the vendored ODCS v3.2.0 JSON Schema, then K2's profile.
2. Each product against its place: its directory, its domain's owner and kind, its outputs
   against the contracts beside it; its name is not one of the schemas every domain catalog
   keeps for the platform (`staging`, `intermediate`, `dq`).
3. Each contract against `lake.sql`: the table exists and its partitioning and sort order are
   the declared ones; or, with inline columns, the table is one `lake.sql` does not create and
   its partitioning and sort name its own columns. Every table has one contract, `audit` tables
   included, since the nightly `contract_schema` audit fails one without.
4. Across products: a table another table reads keeps snapshots for the seven-day floor
   `maintenance.py` enforces; a table is at least as restricted as anything it reads; every
   input resolves to another product's output or a declared external source.

## What `k2gen generate` writes

`generate` runs `validate` first and writes nothing on a problem. It renders every output in
memory, deletes the directories it owns, and writes them again in sorted order, so two runs
are byte-identical and a deleted contract leaves nothing behind.

| Output | Path | Read by |
|---|---|---|
| Iceberg DDL for contracts with inline columns | `docker/lake/ddl/generated/` (owned) | `docker/lake/apply_ddl.py`, after `lake.sql` |
| Snapshot expiry per table, in days | `docker/lake/generated/retention.json` | nothing yet: `maintenance.py` reads it from phase P5 |
| Each lake table as its contract states it: columns in order with their Spark SQL type and `NOT NULL`, partitioning, sort order, identifier fields | `docker/lake/generated/contract_schemas.json` | `maintenance.py`'s nightly `contract_schema` audit, below |
| A page per product, the index by domain, and every product in one table (`all.md`) | `docs/products/` (owned) | people, and [the data catalog](../operations/data-catalog.md#data-products), which links `all.md` instead of keeping its own list; check-docs gate (a) checks every link on them |
| One mart warehouse per domain | `docker/lake/generated/warehouses.txt` | `docker/lake/init-lake.sh`, after the lake's own bootstrap |
| A writable Trino catalog per domain, and its local twin | `docker/trino/etc/catalog/<domain>.properties`, `docker/trino/test/local/<domain>.properties` (the generated ones; `lake.properties` beside them is hand-written) | Trino; `make test-trino` |
| Trino's access rules: only a domain's writer creates, changes or drops its tables and rows with SQL statements (table procedures are not fenced per user) | `docker/trino/etc/rules.json` | Trino ([ADR-044](../adr/ADR-044-producer-lake-and-domain-mart-warehouses.md)) |
| The dbt sources for every lake table a mart reads: Trino column types, freshness from the contract's latency, a uniqueness test on its identifier fields | `dbt/models/_sources.yml` | dbt ([`dbt/README.md`](../../dbt/README.md)); `make test-dbt` |
| One dbt selector per domain with marts, run as its writer | `dbt/selectors.yml` | dbt; `scripts/dbt_manifest_check.py` holds each mart to its contract |

How those warehouses, catalogs and rules work together, and who may read or write each, is
[chapter 19](19-lakehouse-and-domain-warehouses.md).

With no inline-column contract, the DDL directory holds only the `README.md` that `generate`
writes there, which says so. Later phases add outputs (Airflow DAG config, catalog payloads,
SLO rules) at the paths the
[phase P1 output map](../plans/2026-11-24-governed-self-serve-platform/001-contracts-generator-mesh.md)
fixes. How a team adds a product, step by step: [the golden path](../development/golden-path.md).

## Where a contract is enforced, and when

| Check | When | Holds the contract to | Catches |
|---|---|---|---|
| `k2gen validate` (CI job `contracts`, `make test-k2gen`) | every pull request | `lake.sql`, the file | a contract and the DDL file disagreeing on the table, its partitioning or its sort order; a lake table with no contract |
| The drift gate (`make generate`, then no diff) | every pull request | the generated files | a generated file edited by hand, or left stale |
| `contract_schema` in `maintenance.py` ([runbook](../runbooks/lake-audit-failed.md#15-contract_schema-a-live-table-is-not-what-its-contract-states) section 15) | nightly, 03:00 UTC, under the writer lock | the live Iceberg table: columns, types, nullability, order, partition spec, sort order, identifier fields | an `ALTER` run by hand, DDL applied only in part, a contract merged and never applied, a table with no contract outside a `scratch_` namespace |

Each check reports; none refuses anything. The CI checks fail a pull request on an
unprotected `main`, and the nightly audit files a failed row in `audit.checks` and fires
`LakeAuditFailed`, up to a day after the table moved. **Nothing refuses a bad write at write
time.** Iceberg and Spark refuse a write that does not fit the live table's schema, but that
is the live table, which may already differ from its contract; no writer compares what it
writes with the contract. The nightly audit reads catalog metadata only and opens no data file;
`python3 docker/lake/contract_schema.py` runs the same comparison from the host, read-only.

## Practices

| Practice | Where it is enforced |
|---|---|
| Every lake table has exactly one contract | `k2gen validate` step 3; `test_every_table_has_exactly_one_contract`, `test_an_audit_table_without_a_contract_fails` |
| A contract and `lake.sql` agree on partitioning and sort | `k2gen validate` step 3; `test_a_wrong_partition_spec_fails`, `test_a_wrong_sort_order_fails` |
| The live table and its contract agree, column by column | nightly `contract_schema` audit; `tests/test_lake_contract_schema.py`, and `test_contract_schemas_state_every_table_as_its_contract_does` for what it compares against |
| A table another reads keeps the seven-day snapshot floor | `k2gen validate` step 4; `test_a_parent_with_expiry_under_the_floor_fails` |
| A table is at least as restricted as its inputs | `k2gen validate` step 4; `test_a_table_less_restricted_than_its_input_fails` |
| Generated files are never edited by hand | CI job `contracts`: `make generate`, then no diff and no untracked file |
| Generation is repeatable and removes what a deleted contract wrote | `test_a_second_generate_changes_nothing`, `test_a_deleted_contract_removes_its_outputs` |
| A scaffolded product is valid as written | `test_a_scaffolded_product_validates` |
| The product table has one row per product, with its owner and its tables and marts | `test_the_product_table_has_one_row_per_product` |
| A product cannot take a platform schema's name | `k2gen validate` step 2; `test_a_product_named_like_a_platform_schema_is_rejected` |
| A new domain gets its fence; a removed one takes it; a reserved or malformed domain id is refused | `test_a_new_domain_gets_its_fence`, `test_a_removed_domain_takes_its_catalog_and_leaves_lake_properties`, `test_a_domain_whose_fence_name_is_taken_is_rejected`, `test_a_domain_id_that_is_not_a_slug_is_rejected` |
| The generator is typed | mypy, strict, pinned in `platform/k2gen/uv.lock`; `make lint` and CI job `contracts` |

The tests are in [`platform/k2gen/tests/`](../../platform/k2gen/tests/); `make test-k2gen` runs
them and `make test` includes it.

## Trade-offs

- **The contracts of existing tables carry no columns.** A product page takes its column
  descriptions from `lake.sql`'s `COMMENT` clauses, and a column with none has an empty cell.
- **Consistency, not truth.** An `upstreams` list that is wrong but self-consistent passes. A job
  that starts reading a table without its contract saying so is invisible until lineage is
  captured in phase P5.
- **Five teams that are one person.** CODEOWNERS routes no review while every line names the
  same account; the trigger to change that is a second contributor
  ([ADR-033](../adr/ADR-033-data-mesh-four-domains.md), Revisit when).
- **The live check is nightly, not at the write.** A table that drifts from its contract is
  found up to a day later, after rows have been written to it; a write-time refusal would mean
  every writer reading the contract before each commit.
- **`retention.json` is read by nothing yet.** It is written and kept current now so phase P5
  can switch `maintenance.py` to it with values equal to today's.

## Key points

- The unit of ownership is the product, not the table or the layer: five domains, 13 products, one owner each.
- `lake.sql` stays the DDL of record for every table it creates; contracts are checked against it, never the reverse.
- Generated files are committed, owned by `k2gen generate`, and CI fails on any drift.

## Related

- [ADR-032](../adr/ADR-032-odcs-contracts-and-k2gen.md), the contracts and the generator
- [ADR-033](../adr/ADR-033-data-mesh-four-domains.md), the four domains and their owners;
  [ADR-046](../adr/ADR-046-governance-domain-and-quality-results.md), the fifth, governance
- [Product index](../products/README.md), generated: a page per product
- [Golden path](../development/golden-path.md), adding a product
- [`contracts/schema/README.md`](../../contracts/schema/README.md), where each K2 field sits in an ODCS contract
- [09-lake-layers.md](09-lake-layers.md), [13-schema-design.md](13-schema-design.md), [14-partitioning-strategy.md](14-partitioning-strategy.md), the tables the products are made of
