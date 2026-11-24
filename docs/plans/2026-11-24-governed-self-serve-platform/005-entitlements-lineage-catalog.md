# Phase P5: entitlements, retention as code, lineage and the catalog

**Depends on:** P1 to P4
**Decision it implements:** ADR-038 (OpenLineage for lineage, OpenMetadata for the catalog: one decision, the metadata stack) and ADR-039 (classification and entitlements), both written in this phase. ADR-039 lifts [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §8 for the Trino path only and appends an Outcome there.
**Delivers:** access rules generated from each contract, retention read from contracts, lineage events captured from all three engines in the default stack, and a catalog that models the four domains behind an opt-in profile.
**Exit:** `scripts/entitlements-verify.sh` shows `delayed_reader` denied on `lake.gold.trades` and allowed on the delayed product; a test asserts captured lineage events name `raw.messages` as an input and a mart as an output; `maintenance.py` fails loudly when its retention config is missing; `make gates` stays green.

## Scope

**Pull request 1: entitlements and retention, ADR-039**

- `k2gen` emitter: Trino file-based access rules from `classification` and `entitlement`.
  `realtime_reader` reads `gold` and `marts`. `delayed_reader` reads only products with
  entitlement `delayed` or classification `public` (the FX rates and analytics). Neither writes. The `dbt` user writes `marts` only.
- Offline test in `make test`: the generated rules match the contracts (every restricted
  product is absent from `delayed_reader`, every delayed product is present).
- Live test, `scripts/entitlements-verify.sh`: both queries against the stack, asserting the
  denial. The same script runs against the CI Trino with the generated rules.
- What this does and does not enforce, stated in ADR-039 and in the control's wording: the
  rules bind the Trino path. DuckDB, Spark and ClickHouse read the lake with the object-store
  keys and no identity. On a single host with no TLS, Trino also takes the user name from a
  header: the rules are enforced, the identity is asserted.
- Retention: `maintenance.py` reads `docker/lake/generated/retention.json` in place of its
  CLI defaults, keeps its floors, and exits 1 with a clear message if the file is missing.
  Values start equal to today's, so behaviour does not change in this phase.
- `docs/governance/privacy-and-pii.md`, marked design only: classification, tokenisation,
  masking, row deletes and crypto-shredding for erasure, access audit.

**Pull request 2: lineage capture**

- Airflow OpenLineage provider and dbt through cosmos, file transport, always on.
- Spark: measure the ingest driver's peak RSS with the OpenLineage listener on. If it holds
  inside the 2g heap, bake the jar into `docker/spark/Dockerfile`, sha256-pinned like the
  others. If not, Spark lineage stays at job level and ADR-038 says so.
- `tests/test_lineage.py` in `make test`, against committed sample events, checks the parser;
  `scripts/lineage-verify.sh` checks real events from the stack.

**Pull request 3: catalog, stretch, ADR-038**

- Spike first, before any compose change: one Spark write and one dbt model produce one
  lineage edge in OpenMetadata. If no edge appears, stop here: the payloads below are
  generated and validated offline and the service stays out.
- OpenMetadata and its search engine under `--profile governance`, pinned, limits declared,
  its database on the shared Postgres with a per-role connection limit and a small pool.
- Ingestion as a one-shot container running the OpenMetadata CLI, not inside Airflow's
  Python environment.
- `k2gen` emitter: domain, data product, owner, tag and glossary payloads. DAG
  `catalog_sync` applies them and loads Trino metadata, dbt artefacts and
  `governance.dq_results`.
- Lineage reaches the catalog through an HTTP transport switched on by one `.env` variable,
  so the default stack makes no connection attempts.
- The profile is outside the default budget and needs a 50 GB Docker engine.
  `docs/development/setup.md` says so, with the steps.

**Documents.** Chapters 11 and 18; a runbook for an access denial that should have been
allowed; ADR-038's measured figures for the profile.

## Verification

```bash
make generate && test -z "$(git status --porcelain)"
bash scripts/entitlements-verify.sh
bash scripts/lineage-verify.sh
docker exec k2-spark-iceberg sh -c 'mv /home/iceberg/lake/generated/retention.json /tmp/r && python3 /home/iceberg/lake/maintenance.py --audit-only; echo "exit=$?"; mv /tmp/r /home/iceberg/lake/generated/retention.json'   # exit=1
make gates
docker compose --profile governance up -d && make health     # stretch, 50 GB engine
```
