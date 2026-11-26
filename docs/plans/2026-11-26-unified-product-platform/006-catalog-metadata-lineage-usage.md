# Phase 006: catalog, metadata, lineage and usage

**Depends on:** `005` (tag `v3.0.0-alpha.7`: products in three domains and the joins between
them); `004`'s Kafka connector, which lands the governance topic.
**Decision it implements:** `docs/adr/ADR-056-unified-data-product-platform.md`: metadata is a
first-class output of every step, in open standards where a mature one exists; the owner's
decision on the catalog site. New ADRs, each in the pull request that builds it: usage capture
from Trino's event listener; lineage events through the governance topic; retention as code,
which lifts section 12 of [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) (unless the
landing-retention ADR of `004` already did) and appends an Outcome there.
**Delivers:** a grant and expectation for every product; the full catalog site; a
`governance.query-usage` product fed by Trino's Kafka event listener; OpenLineage events from
Airflow and dbt; lineage across domains from ports, the dbt manifest, run records and events,
served at `/v1/lineage/{product}`; the 30-day no-query rule before retirement; retention as code;
a privacy note on the usage records of pseudonymous people.
**Exit:** `make access-verify` passes over every expectation of every product; the upstream
lineage of `candles-eur` crosses three domains; a query by a test principal appears in usage
within one tick; a retirement of a product queried in the last 30 days is refused; the catalog
site lists and finds every product and column, each page with its lineage diagram, quality and
readers; tag `v3.0.0-alpha.8`.

## Scope

**Every grant.** Each product that exists before this phase gets its grants and its rows in
`tests/access/expectations.yaml`; the coverage rule already refuses a policy without one. Products
added later bring their own (plan review section 1).

**The catalog site, complete** (owner decision: a generated, read-only static site, no
off-the-shelf catalog tool). The generator from `003` gains search over products, columns and
join keys; per product a lineage diagram, quality from the governance tables, who can read
(principal and role), example queries and join keys; links to the dbt docs. It reruns after each
deploy and on a schedule, served by `k2-api` at `/catalog/`. This is the one rendering of the
catalog (review trap 8).

**Usage.** Trino's Kafka event listener (the plugin is in `trinodb/trino:484`) sends a
query-completed event per query (principal, tables, columns) to a governance topic on Redpanda;
the platform's own Kafka connector lands it as the product `governance.query-usage`. Principals
are pseudonymous ids; a privacy note says what is recorded about people, who reads it and how
long it is kept.

**Lineage.**

- Product level from ODPS ports and model level from the dbt manifest, in the release; the
  reference snapshot each enriched run read, from run records.
- OpenLineage events from the Airflow provider and from `dbt-ol`, into the same governance topic
  and landed the same way. Whether to keep this source or rely on ports, manifest and run
  records, which already give the demo its lineage across domains, is the owner's choice here,
  default kept as written (plan review section 6).
- `GET /v1/lineage/{product}?direction=up|down` serves the graph; the catalog page draws it.

**Lifecycle and retention.**

- A retirement needs deprecation first, the sunset passed, no declared reader, and no query in 30
  days, read from usage; until now declared readers were the only signal.
- Retention as code: each table's declared `retention` compiles into the release and the
  per-product maintenance applies it, never below the floor that rollback and the 7-day `as_of`
  window need.

**Learning aids and documents.** Runbook: verify access end to end. Chapter 26 (metadata and
standards).

## Verification

```bash
make access-verify                                               # every expectation of every product
curl -s '127.0.0.1:8095/v1/lineage/candles-eur?direction=up'     # nodes in three domains
bash scripts/verify/usage-tick.sh            # a test principal's query appears in query-usage within one tick
make test-k2gen                              # retiring a product queried within 30 days fails (fixture)
bash scripts/verify/retention.sh             # a fixture table's snapshots past its retention expired, run record says so
bash scripts/verify/catalog-site.sh          # every product and column in the search index; each page has lineage, quality, readers
make check-docs
make test
```
