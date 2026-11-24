# Phase P5: access points, entitlements, lineage and the catalog

**Depends on:** P1 to P4
**Decision it implements:** ADR-038 (OpenLineage for lineage, OpenMetadata for the catalog: one decision, the metadata stack) and ADR-039 (serving as declared access points on one catalog; classification and entitlements), both written in this phase. ADR-039 lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §8 for the Trino path and appends an Outcome there.
**Delivers:** every product reachable through declared, tested access points, including any Iceberg client a consumer brings; access rules generated from contracts; retention read from contracts; lineage from source file to access point; and a catalog that shows all of it.
**Exit:** `make access-verify` passes every declared access point of every published product; `scripts/entitlements-verify.sh` shows `delayed_reader` denied on `lake.gold.trades` and allowed on the delayed product; a consumer recipe using the read-only key can read and cannot write; `scripts/lineage-verify.sh` finds an unbroken chain from the ECB file to `fx_rates_daily`; with the `governance` profile up, the FX product appears in the catalog under its domain with owner, lineage and quality; `make gates` stays green.

## The serving model

Serving is not a service in front of the data. Tables are open Iceberg behind one REST
catalog, and a product is served by every way of reaching it that its contract declares.

| Access point | For | How it is reached | Entitlement enforced |
|---|---|---|---|
| Trino SQL | Analysts, BI, dbt | catalog `lake` | Yes, from the contract |
| DuckDB, notebook helper | Researchers, pinned snapshots | `k2lake` over the REST catalog | No: read-only key |
| ClickHouse hot tier | Dashboards, low latency | native, for products loaded there | Read-only profile, all of `gold` |
| Open catalog endpoint | Any Iceberg client the consumer brings: PyIceberg, Spark, Polars, another engine | the REST catalog URI and the read-only key | No: read-only key |

The last row is what "bring your own compute" means here: the platform does not choose the
consumer's engine. It publishes an open table, a catalog address, a schema with a
compatibility promise, and a tested recipe.

What is and is not enforced is stated wherever access is described. Entitlements bind the
Trino path. Every other path reads with one read-only storage key that can see every table,
because storage paths are keyed by table id and cannot be scoped by namespace. Closing that
gap needs credentials issued per table by the catalog. ADR-039 records that as the target
design (an identity provider, Lakekeeper's authorisation, short-lived storage credentials, no
static keys in any consumer's hands) and a follow-on plan builds it. The same shape is what a
cloud deployment would use.

## Scope

**Pull request 1: access points and bring-your-own-engine, ADR-039**

- Each output contract's `servers` section lists its access points. `k2gen` checks that a
  ClickHouse access point is only declared for a table the hot tier loads, and emits
  `generated/access-points.json`.
- A read-only MinIO user and policy (`GetObject` and `ListBucket` on the lake bucket),
  created by `docker/lake/init-lake.sh`. Every consumer recipe, the notebook helper and the
  parity scripts use it. The root key stays with the writers.
- `make access-verify`: for each published product and each declared access point, one read
  that returns rows and matches the contract's column list. PyIceberg is the client for the
  open endpoint, as a stand-in for any spec-compliant engine. A write attempt with the
  consumer key must fail.
- `docs/development/bring-your-own-engine.md`: the catalog address, the consumer key, and
  recipes that were run for PyIceberg, DuckDB, Spark and Trino; snapshot pinning for
  reproducible reads; what the schema compatibility promise covers; and the enforcement
  statement above.
- `k2gen` emitter: Trino file-based access rules from `classification` and `entitlement`.
  Roles follow classification, not namespace: `realtime_reader` reads every product classified
  `internal` or `public`; `delayed_reader` reads products classified `public` or with
  entitlement `delayed`. Neither
  writes. The `dbt` user writes `marts` only.
- Offline test in `make test`: the generated rules match the contracts. Live test,
  `scripts/entitlements-verify.sh`, against the stack and against the CI Trino.
- On a single host with no TLS, Trino takes the user name from a header: the rules are
  enforced, the identity is asserted. ADR-039 says so.
- Product pages gain a "How to read this" section generated from the access points.

**Pull request 2: retention as code**

- `maintenance.py` reads `docker/lake/generated/retention.json` in place of its CLI defaults,
  keeps its floors, and exits 1 with a clear message if the file is missing. Values start
  equal to today's, so behaviour does not change in this phase.
- `docs/governance/privacy-and-pii.md`, marked design only: classification, tokenisation,
  masking, row deletes and crypto-shredding for erasure, access audit.

**Pull request 3: lineage, source to access point**

- Airflow OpenLineage provider and dbt through cosmos, file transport, always on. The
  file-source DAG emits the external URL and the landing object as datasets, so the chain
  starts outside the platform.
- Spark: measure the ingest driver's peak RSS with the OpenLineage listener on. If it holds
  inside the 2g heap, bake the jar into `docker/spark/Dockerfile`, sha256-pinned like the
  others. If not, the file-source loader emits its own two events (it knows its input object
  and output table) and the streaming ingest stays at job level; ADR-038 says which.
- `tests/test_lineage.py` in `make test` checks the event parser against committed samples.
  `scripts/lineage-verify.sh` asserts the chain on the stack: ECB URL, landing object,
  bronze, silver, each FX model.
- Product pages gain a lineage diagram generated from the captured events.

**Pull request 4: the catalog, ADR-038**

- Spike first, before any compose change: one Spark write and one dbt model produce one
  lineage edge in OpenMetadata. If no edge appears, the fallback in the plan's risk table
  applies and ADR-038 records it.
- OpenMetadata and its search engine under `--profile governance`, pinned, limits declared,
  its database on the shared Postgres with a per-role connection limit and a small pool.
- Ingestion as a one-shot container running the OpenMetadata CLI, not inside Airflow's
  Python environment.
- `k2gen` emitter: domain, data product, owner, tag and glossary payloads from the
  descriptors and contracts. DAG `catalog_sync` applies them and loads Trino metadata, dbt
  artefacts, `governance.dq_results` and the lineage events.
- Lineage reaches the catalog through an HTTP transport switched on by one `.env` variable,
  so the default stack makes no connection attempts.
- The profile is outside the default budget and needs a 50 GB Docker engine.
  `docs/development/setup.md` says so, with the steps.

**Documents.** Chapters 11 and 18; a runbook for an access denial that should have been
allowed; ADR-038's measured figures for the profile.

## Verification

```bash
make generate && test -z "$(git status --porcelain)"
make access-verify
bash scripts/entitlements-verify.sh
bash scripts/lineage-verify.sh
docker exec k2-spark-iceberg sh -c 'mv /home/iceberg/lake/generated/retention.json /tmp/r && python3 /home/iceberg/lake/maintenance.py --audit-only; echo "exit=$?"; mv /tmp/r /home/iceberg/lake/generated/retention.json'   # exit=1
make gates
docker compose --profile governance up -d && make health
docker exec k2-airflow-scheduler airflow dags test catalog_sync
```
