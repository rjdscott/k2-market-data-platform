# The golden path: adding a data product

How a team adds a product to the lake, as far as the platform supports it today. Started in
phase P1 of the [governed self-serve platform plan](../plans/2026-11-24-governed-self-serve-platform/README.md),
which covers the first step, **declare**. Phase P7 finishes this page by following it from
zero with a real product. Every command below was run on 2026-11-25 and its output pasted.
What domains, products and contracts are: [chapter 18](../architecture/18-data-mesh.md).

## 1. Scaffold

```bash
make new-product DOMAIN=market-analytics NAME=example
```

```
cd platform/k2gen && uv run --locked -q k2gen new "market-analytics" "example"
k2gen new: wrote contracts/market-analytics/example/product.yaml
k2gen new: wrote contracts/market-analytics/example/gold.example.odcs.yaml
k2gen new: edit both, then `make generate` and commit what it writes
```

`DOMAIN` is one declared in [`contracts/domains.yaml`](../../contracts/domains.yaml), and the
descriptor takes that domain's owner and kind. `NAME` is lower-case letters, digits and
hyphens. The contract is for a new table, `gold.<name>` in a consumer-aligned domain and
`bronze.<name>` in a source-aligned one, with two placeholder columns inline, partitioned by
`days(event_ts)` and sorted by `event_ts`. Anything else is refused, exit 2 from `make`. From
`make new-product DOMAIN=market-research NAME=example`, then the first command again, `k2gen`'s
own lines:

```
k2gen new: market-research is not a domain in contracts/domains.yaml: market-capture, reference-data, fx-reference, market-analytics
k2gen new: contracts/market-analytics/example already exists
```

## 2. Describe it, and validate

Edit both files. In `product.yaml`: the purpose, the inputs (another product's output as
`table:`, or a source outside the lake as `external:` with its classification), the known
consumers. In the contract: the columns (`physicalType` as Spark SQL spells it, a
description each), partitioning, sort, classification, entitlement, retention, the tables each
output reads (`upstreams`), and the schedule of the job that will write it. The field map is
[`contracts/schema/README.md`](../../contracts/schema/README.md).

```bash
uv run --project platform/k2gen k2gen validate
```

```
k2gen validate: 14 products, 27 contracts, every lake table covered
```

The scaffold passes as written. The rules that bite once inputs are added are in
[chapter 18](../architecture/18-data-mesh.md#what-k2gen-validate-checks): a product is at least
as restricted as what it reads, and every input is another product's output or a declared
external source.

## 3. Generate

```bash
make generate
git status --short
```

```
cd platform/k2gen && uv run --locked -q k2gen generate
k2gen generate: 24 files: docker/lake/generated/retention.json, docker/lake/generated/warehouses.txt, docker/trino/etc/rules.json, the domain catalogs in docker/trino/etc/catalog and docker/trino/test/local, and under docker/lake/ddl/generated, docs/products
 M docker/lake/ddl/generated/README.md
 M docker/lake/generated/retention.json
 M docs/products/README.md
?? contracts/market-analytics/example/
?? docker/lake/ddl/generated/gold.example.sql
?? docs/products/market-analytics/example.md
```

The new table's DDL, its snapshot expiry, its product page and a line in the
[product index](../products/README.md). A contract that fails validation writes nothing. With
the partitioning changed to `days(recv_ts)`, `make generate` exits 2 and prints:

```
contracts/market-analytics/example/gold.example.odcs.yaml: partitioning days(recv_ts) reads no column the contract declares
k2gen generate: 1 problem(s), nothing written
```

## 4. Open a pull request

Commit the contracts and everything `make generate` wrote. CI's `contracts` job runs
`make generate` again and fails on any diff or untracked file, so a generated file edited by
hand, or one left out of the commit, does not merge.

## Not on the path yet

| Step | What it needs | Phase |
|---|---|---|
| Create the table in the lake | `docker/lake/apply_ddl.py` applies `ddl/generated/*.sql` after `lake.sql` (`tests/test_lake_apply_ddl.py`). Not yet run against a stack on this path | P1 code, P7 proof |
| Ingest | a generated Airflow DAG and the file-source pattern | P4 |
| Process | dbt models and tests: the sources and selectors are generated from a product's `marts` ([`dbt/README.md`](../../dbt/README.md)); a scaffold for a new mart is not | P3, P4 |
| Trace, serve, catalogue | lineage, access rules, catalog payloads | P5 |
| Publish | the readiness scorecard that gates `lifecycle: published` | P6 |

## Removing a product

```bash
rm -r contracts/market-analytics/example
make generate
git status --porcelain | wc -l
```

```
cd platform/k2gen && uv run --locked -q k2gen generate
k2gen generate: 22 files: docker/lake/generated/retention.json, docker/lake/generated/warehouses.txt, docker/trino/etc/rules.json, the domain catalogs in docker/trino/etc/catalog and docker/trino/test/local, and under docker/lake/ddl/generated, docs/products
0
```

Its DDL, retention entry, page and index line go with it.
