# Plan: K2 as a governed, self-serve platform

> Design document and phase gates. Status lives in ADRs, git tags and CI, not here.

## Context

K2 is a single-team platform that works: Rust capture, Redpanda, a Spark batch ingest into an
Iceberg lake on MinIO under Prefect, a Lakekeeper catalog, and ClickHouse serving gold. What it
does not show is the layer above that: a platform other teams build on. The wire has a contract
(`schemas/avro/`, `tests/test_contracts.py`); a data product does not. There is no paved road
for a new product, no ownership model, no catalog, no compliance evidence, and access control
stops at one read-only ClickHouse profile.

This plan extends K2 in place, crypto domain only, to add that layer: Airflow 3 replacing
Prefect, Trino and dbt marts beside the Spark ingest, a contract-driven generator, four data
mesh domains, lineage and a catalog, layered data quality, entitlements, a controls catalogue,
SLOs, release engineering and a governance document pack. The bar is unchanged: it runs from a
fresh clone, and every claim has a command behind it.

Umbrella decision: [ADR-031](../../adr/ADR-031-governed-self-serve-platform.md).

**Out of scope, and where it goes**

- AWS infrastructure code and the AWS document set: a separate plan, written after this one
  starts. [17-scale-out-path.md](../../architecture/17-scale-out-path.md) remains the cloud
  design until then, and [ADR-028](../../adr/ADR-028-non-goals-and-honest-limits.md) §9
  stays in force.
- A REST data product API. ADR-028 §6 stays in force for an API; this plan lifts it only for
  SQL serving through Trino.

## Decisions

**Decisions (user-confirmed, 2026-11-24):**

- Airflow 3 replaces Prefect entirely. Cutover is a schedule shadow (Airflow runs the ingest
  in its write-nothing probe mode on the real cron), then a direct switch behind the existing
  writer lock, with Prefect kept as a rollback until the gates hold. A data shadow on an
  Iceberg branch was considered and rejected (Ground truth, "Branches").
- dbt on Trino over the same Iceberg tables. Spark keeps raw to gold, backfill and compaction;
  dbt owns new consumer-aligned marts. One gold aggregate is ported as a parity example.
- Four domains: `market-capture`, `reference-data`, `fx-reference` (new, ECB XML),
  `market-analytics`. The platform team owns the generator, Airflow, Trino, the catalog and
  the controls.
- One YAML contract per data product; a generator (`k2gen`) emits the governed artefacts;
  outputs are committed and CI fails on drift.
- Layered quality: CI, an audit at ingest, dbt tests on models, reconciliation.
  Write-audit-publish on a branch applies to the FX ingest and fails closed. The
  `raw.messages` ingest gets a per-batch audit that always publishes, records the finding and
  alerts: the system of record must not stall behind its own audit.
- OpenLineage for lineage, captured to file in the default stack. OpenMetadata for the
  catalog behind an opt-in `governance` compose profile.
- Classification and entitlement (realtime or delayed) drive access rules on the Trino path.
  A design-only document covers personal data.
- A controls catalogue as code, with daily evidence and a generated report.
- **Resource budget.** The default profile's ceiling is 20 CPU / 36 GiB, counted at the
  bootstrap peak and enforced in CI. The `governance` profile is outside that ceiling and
  needs a Docker engine of 50 GB. The 16 CPU clause of
  [ADR-010](../../adr/ADR-010-resource-budget.md) is superseded by ADR-034.
- **ADR sequencing:** ADR-031 is the Proposed umbrella and reserves 032 to 042. Each decision
  ADR lands Accepted in the pull request that builds it, as
  [the ADR conventions](../../adr/README.md) require.
- **A capability matrix** at `docs/capability-traceability.md`: one row per platform
  capability, grouped by area, each with its evidence and the command that shows it.
- New text carries no em-dashes. New ADRs use the status `Accepted` and the landed line uses a
  comma. Existing documents are not swept: Accepted ADRs are immutable.

## Ground truth

Verified against `main` at `abdc804`.

**Orchestration**

- Two Prefect flows exist: `lake-ingest` (`docker/lake/flows/lake_flows.py:108`, cron
  `1-59/5 * * * *`, `deploy_lake.py:47`) and `lake-maintenance` (`lake_flows.py:113`, cron
  `0 3 * * *`, `deploy_lake.py:73`). Both are a `docker exec` into `k2-spark-iceberg`
  (`lake_flows.py:63`). Audits, including the nightly candle and bar parity
  (`docker/lake/maintenance.py:560`, `:594`), run inside maintenance. Metrics is an exporter;
  rebuild is a script (`Makefile:89-90`).
- Mutual exclusion is a `flock`, not the orchestrator (`docker/lake/lock.py:17-31`). An ingest
  that finds the lock held exits 2 (`ingest.py:498-507`). Exit 2 is also what Python returns
  for a missing script and what argparse returns for a bad flag, so 2 cannot be mapped to
  "skipped" safely.
- `ingest.py --probe` reads the topics, writes nothing and does not take the lock
  (`ingest.py:492-499`).
- Exactly-once state is in the Iceberg snapshot summary (`docker/lake/offsets.py:34-53`,
  commit at `ingest.py:325-332`). Nothing about data position lives in Prefect.
- `prefect-db` also hosts the Lakekeeper catalog database (`docker-compose.yml:592-593`,
  `docker/postgres/ddl/10-lakekeeper-db.sql:24`). The role and database inside existing
  volumes are named `prefect` (`.env.example:23-25`).
- `lake-metrics` runs on the Prefect worker image (`docker-compose.yml:760-778`), reads only
  REST metadata, and has a 128M limit (`docker/lake/metrics.py:16-27`).
- Schedule pause and resume is scripted against the Prefect CLI
  (`scripts/chaos/lib.sh:169-240`), used by `scripts/lake-verify.sh` and four chaos scripts.
  `scripts/health.sh:109-138` reads Prefect run history over an unauthenticated REST call.
- `git grep -li prefect` outside `legacy/` and the dated document surfaces returns 52 files.

**Branches**

- Resume reads every snapshot of the table, on any ref, and takes the newest with
  `k2.job == ingest` (`docker/lake/catalog.py:23-28`, `offsets.py:274-277`). A commit to any
  branch of `raw.messages` would therefore move where `main` resumes, and `main` would skip
  offsets. The exporter (`metrics.py:249-252`), `scripts/lake-verify.sh:123`,
  `notebooks/k2lake.py:84` and `scripts/parity_ohlcv.py:66-69` use the same newest-snapshot
  rule.
- No branch is written anywhere today. The only ref read is `main` (`catalog.py:32-43`).
- Consequence for this plan: no branch is ever written on a table the ingest resumes from.
  Branches are used only on the new FX tables, and `k2lake.pin()` is fixed to follow `main`
  before the first one is written.

**Budget**

- Declared steady state is 14.60 CPU / 25.625 GiB across 15 services; bootstrap peak 16.10 CPU
  / 27.125 GiB across 19 (`docker compose --env-file .env.example config`, matching the latest
  Outcome in ADR-010). Limits are in GiB (`docs/operations/docker-resources.md:46-49`).
- The Docker engine on the reference host reports 42,051,227,648 bytes, which is 39.16 GiB,
  and 28 CPUs (`docker info`).
- CI fails a service with no limits but does not check the sum, and does not see services
  behind a profile (`.github/workflows/ci.yml:141-156`).
- Host port 8080 is published by `redpanda-console` (`docker-compose.yml:127`).
  `scripts/dev-up.sh:31-46` remaps a port only when it is busy before `up`, so it cannot
  resolve a clash inside the compose file.
- Stale today: the compose summary lists `spark-iceberg` at 4G (`docker-compose.yml:1008`,
  limit is 8G at `:555`); required engine memory is 28 GB in `docs/development/setup.md:10`
  and 24 GB in `docs/runbooks/fresh-install.md:18`; `README.md:86` says 27 ADRs.

**Lake and contracts**

- `docker/lake/ddl/lake.sql` declares columns, comments, partitioning and sort order.
  Identifier fields are also held in Python (`bronze.py:86`, `silver.py:153`, `gold.py:68`,
  `books.py:126`). Namespaces: `raw`, `bronze`, `silver`, `gold`, `audit`
  (`docker/lake/init-lake.sh:122`).
- Only `lake.sql` is ever applied (`docker/lake/apply_ddl.py:24`), and the Spark container
  mounts only `./docker/lake` (`docker-compose.yml:528`). Generated files the lake jobs read
  must live under that directory.
- Two near-identical DDL column parsers exist in tests (`tests/test_wire_format.py:33`,
  `tests/test_lake_scd2.py:205`).
- `gold.ohlcv_1m` is one SQL `GROUP BY` (`docker/lake/gold.py:316`). Its three-way parity
  check compares open, high, low, close, trade count and volume (`scripts/parity_ohlcv.py:149`).
- Audits write `lake.audit.checks` (`maintenance.py:685-760`). The offset-continuity audit is
  a whole-table scan, run nightly only (`offsets.py:296-299`).
- Retention values are CLI defaults (`maintenance.py:765-772`) with floors (`:775-778`).
  Expiry and orphan removal loop over hard-coded table lists (`:169`, `:249`). The seven-day
  floor exists because a child layer resumes from its parent's snapshot id (`:163-166`).
- Lakekeeper is `v0.13.3`, unauthenticated, no credential vending
  (`docker-compose.yml:621-639`, `init-lake.sh:89`). MinIO has root keys only. DuckDB, Spark
  and ClickHouse all read the lake with those keys.
- Spark 3.5.5, Iceberg 1.8.1, base image pinned by digest (`docker/spark/Dockerfile:18`).
  Jars are baked and sha256-pinned (`:49-61`). The image has no pip layer.
- Python has no root project. `notebooks/` is the pattern for a self-contained package.

**Drift between the places a product is described**, the problem ADR-031 starts from:

| Place | Says | Actually |
|---|---|---|
| `docs/operations/data-catalog.md:191-193` | candle ordering at `gold.py:310,313` | it is at `gold.py:328,331` |
| `docker/lake/gold.py:93-94` | `test_wire_format` checks the dimension columns | the check is `tests/test_lake_scd2.py:227` |
| `lake.sql:1312` and `data-catalog.md:288` | a short list of audit check names | `maintenance.AUDITS` emits more |
| `docker/lake/README.md:18` | the unified bronze pair exists | dropped (`lake.sql:107-109`) |
| `docker-compose.yml:1008` | `spark-iceberg` limit 4G | 8G (`:555`) |
| `docs/architecture/A1-technology-stack.md:75` | no query engine beyond ClickHouse and Spark SQL | notebooks use DuckDB |

**Documents and gates**

- `scripts/check-docs.sh` has eight failing gates. Gate (a) resolves every relative markdown
  link. Gate (b) rejects four words (`:60`). Gate (c) runs `promtool check rules` over
  `rules/*.yml`; rule tests are globbed from `rules/tests/*_test.yml` (`:70-99`).
- CI runs `pytest tests` with no stack (`ci.yml:68-69`); `tests/test_contracts.py:21` states
  that as the rule for that suite.
- The highest ADR is 030. Architecture chapters run 00 to 17 plus A1.
- [ADR-008](../../adr/ADR-008-eliminate-prefect-orchestration.md) rejected Airflow as heavier
  than Prefect (`:100-104`). [ADR-006](../../adr/ADR-006-spark-batch-only.md) rejected Trino
  as a replacement for Spark in batch ETL (`:202-207`).
- [ADR-029](../../adr/ADR-029-research-production-parity-contract.md) requires a second and
  third computation for every materialised product (`:61-62`).
- ADR-028 non-goals this plan touches: FX (§4), a serving layer beyond ClickHouse SQL (§6),
  auth (§8), error budgets, whose trigger is a recorded window of 24 h or more (§10), schema
  automation (§11), a retention policy (§12). A lifted limit needs a new ADR and an appended
  Outcome there (`:285`).
- The `release-check` skill still queries the dropped `k2.silver_trades`
  (`.claude/skills/release-check/SKILL.md:44-50`).

## Target architecture

```mermaid
flowchart TB
    C[contracts/ YAML per data product] --> G[k2gen generator]
    G --> A[Airflow 3]
    G --> R[DDL, retention, access rules, SLO rules, catalog payloads]

    CAP[Rust capture] --> RP[Redpanda]
    RP --> SP[Spark ingest: raw, bronze, silver, gold]
    ECB[ECB FX XML] --> FX[Spark FX ingest: write, audit, publish]
    A --> SP
    A --> FX
    A --> DBT[dbt on Trino]

    SP --> L[(Iceberg lake: Lakekeeper + MinIO)]
    FX --> L
    L --> DBT
    DBT --> M[(marts namespace)]
    RP --> CH[(ClickHouse gold, hot tier)]

    M --> T[Trino: realtime_reader, delayed_reader]
    L --> T
    A --> OL[OpenLineage events, file]
    DBT --> OL
    SP --> OL
    OL -.-> OM[OpenMetadata, governance profile]
    A --> EV[(governance.control_evidence)]
    EV --> REP[compliance report + Grafana]
```

Principles the diagram encodes:

- **The contract is the interface.** A workload engineer writes one YAML file. Everything
  governed about the product is generated from it, committed, and checked for drift.
- **One writer path per layer.** Spark writes raw to gold, FX and `governance`. Trino writes
  only `marts`. Neither writes the other's namespaces.
- **The orchestrator holds no data state.** Offsets stay in the Iceberg snapshot
  ([ADR-022](../../adr/ADR-022-exactly-once-via-snapshot-offsets.md)); Airflow schedules and
  observes.
- **Existing DDL stays the record.** Contracts for existing tables reference `lake.sql` and
  are validated against it. The generator emits DDL only for new products.
- **The default stack proves the claim.** Anything behind the `governance` profile is extra;
  lineage capture, entitlements and controls work without it.

## Phases

| File | Phase | Scope | Exit |
|---|---|---|---|
| `000-prerequisites-guardrails.md` | P0 | Stack up, `make gates`, budget gate, lock exit code, Lakekeeper patch, stale facts | `make gates` green on the live stack; CI budget gate live |
| `001-contracts-generator-mesh.md` | P1 | Contracts for every existing product, `k2gen`, four domains | drift check clean in CI |
| `002-airflow-replaces-prefect.md` | P2 | Airflow 3, schedule shadow, cutover, Prefect archived | `make gates` green under Airflow; Prefect only where listed |
| `003-trino-dbt-fx.md` | P3 | Trino, dbt marts, FX domain with write-audit-publish, quality results | dbt and Spark `ohlcv_1m` agree on the compared columns; a bad FX file never reaches `main` |
| `004-entitlements-lineage-catalog.md` | P4 | Access rules, retention as code, lineage capture, catalog | `delayed_reader` denied on `gold.trades` through Trino; lineage events asserted |
| `005-controls-slos-release.md` | P5 | Controls catalogue, SLO rules, release engineering, drills | `make compliance-report` renders from real evidence |
| `006-governance-pack-release-gate.md` | P6 | Governance documents, capability matrix, fresh-clone gate | `/release-check` passes from a fresh clone |

Order is dependency, then risk. P0 exists because three later phases would otherwise each
discover the same broken guardrail. Each phase file lists its pull requests; an ADR lands in
the pull request that builds its decision.

**Must-have and stretch.** Everything in the phase files is must-have except four items, cut
in this order if time runs out: the OpenMetadata service (lineage capture to file stays),
blue/green dbt deploys, the DR drill benchmark, the chaos extensions. A control whose evidence
source was cut reports "not applicable" with the reason, never a pass. Never cut: the
failure-mode tests, the runbooks, the provenance of a published number.

**Estimate.** About eight working days for the must-have path, live-stack gates included.
This is a judgement, not a measurement.

**Resource budget.** Limits to declare, estimated; each phase measures and records the real
figures in its ADR.

| Change | CPU | GiB | Phase |
|---|---|---|---|
| Today, steady state | 14.60 | 25.625 | |
| Prefect server and worker removed | -1.50 | -1.500 | P2 |
| Airflow api-server | +1.00 | +1.500 | P2 |
| Airflow scheduler (LocalExecutor, parallelism 4) | +1.50 | +3.000 | P2 |
| Airflow dag-processor | +0.50 | +1.000 | P2 |
| statsd exporter | +0.10 | +0.064 | P2 |
| Postgres, more connections | 0 | +0.500 | P2 |
| Trino, single node, heap capped | +2.00 | +4.000 | P3 |
| **Default profile, steady** | **18.20** | **34.189** | |
| One-shots: the four existing and an Airflow migrate | +2.00 | +2.500 | |
| **Default profile, bootstrap peak** | **20.20** | **36.689** | |
| `governance`: OpenMetadata server, search engine, ingestion, Postgres | +2.50 | +5.500 | P4 |
| `governance` one-shot: migrate | +0.50 | +1.500 | P4 |
| **With governance, bootstrap peak** | **23.20** | **43.689** | |

The default bootstrap estimate is 0.20 CPU and 0.7 GiB over the ceiling. P2 and P3 must trim
to fit (the existing one-shots are the first place to look) or the pull request fails its own
gate. No Airflow triggerer: nothing in this plan is deferrable.

## Verification (end-to-end)

```bash
make up && make health
make gates            # test, lake-verify, parity-ohlcv, parity-bars, check-alerts, check-docs
make generate && test -z "$(git status --porcelain)"
make lint
docker exec k2-airflow-scheduler airflow dags list
docker exec k2-airflow-scheduler airflow dags test dbt_marts
bash scripts/entitlements-verify.sh
make compliance-report
```

Then `/release-check` on a fresh clone. With a 50 GB engine:
`docker compose --profile governance up -d`.

## Risks / verify-first

| Unknown | Spike | Escape hatch |
|---|---|---|
| Lakekeeper 0.13.3 is behind its own patch line, which carries ref-handling fixes | P0: move to the newest 0.13.x, run `make gates` | Stay on 0.13.3; FX write-audit-publish becomes a staging table swapped by rename |
| Branch write and fast-forward from Spark 3.5.5 with Iceberg 1.8.1, on a scratch table; readers see only `main` | First task of P3's FX pull request | The staging-table swap above |
| Trino Iceberg REST connector against Lakekeeper: warehouse name, views, DuckDB reading a Trino-written table | First task of P3 | Delayed product as a scheduled dbt table; pin an older Trino |
| The newest Airflow 3 release is weeks old; cosmos and the OpenLineage provider may trail it | P2: pin the newest release cosmos supports outside beta | Pin one minor back |
| dbt and Airflow dependencies clash in one image | P3 | Run dbt in its own container through `docker exec`, as Spark is |
| `quote_volume` precision differs between Spark and Trino decimals | P3: `typeof` on both sides | Leave it out of the compared columns and say so |
| OpenLineage Spark listener memory in a 2g driver that has hit OOM before | P4: measure peak RSS with it on | Dataset lineage from dbt and Airflow only; Spark at job level |
| One Spark write and one dbt model produce one lineage edge in OpenMetadata | P4, before the service is added to compose | Catalog payloads generated and validated offline; service stays out |
| One Postgres at 1 CPU serves Lakekeeper, Airflow and later the catalog | P2: cap connections per role, measure | A second Postgres for the governance profile |
| A `docker exec` timeout kills the client, not the Spark driver, which keeps the lock | P2 | Timeout handler kills the recorded driver PID inside the container |
| Fresh-clone time grows well past the measured 15 minutes ([fresh-install](../../runbooks/fresh-install.md)) | P6 | Publish the measured time |
