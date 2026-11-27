# Changelog

All notable changes to this platform are recorded here, in the
[Keep a Changelog 1.1](https://keepachangelog.com/en/1.1.0/) format.

A version names one tested set of everything in this repository: capture, the lake,
Trino, dbt, Airflow, the contracts and the gates, under one
[SemVer](https://semver.org/) number. An alpha may change anything, including table
layouts and commands, without notice. `3.0.0` is reserved for the release gate that ends
[the plan to release](docs/plans/2026-11-26-unified-product-platform-to-release/README.md);
`3.0.0-beta.1` marks the demo point of
[the unified product platform plan](docs/plans/2026-11-26-unified-product-platform/README.md).

## [Unreleased]

## [3.0.0-alpha.3] - 2026-11-27

Phase 001 of the unified product platform plan, "the release and rules spine": the platform runs only sealed releases compiled from documents, and Trino
decides who sees what by rules compiled from them, behind passwords over TLS. No product
pipeline runs yet; the run spine (phase 002) brings the first.

### Decided

- Readers see a dbt output only through a generated view over its tested tag, owned by a
  publishing identity, its policies as Trino rules on the view's name (ADR-061); the view
  guards on its base's refs, so a forged base serves nothing. ADR-056's rule against
  definer views is narrowed to that one shape.
- Access rules are compiled per release and deny by default, over a read-only reader catalog
  and a build catalog per domain; the catalog service moves onto a network of its own
  (ADR-059, superseding ADR-044's one writer per domain).
- Every Trino login is a password over HTTPS under a local certificate authority (ADR-060).
- Releases are deployed by moving a pointer per service and rolled back by the same steps
  (ADR-058).
- Products are defined once in ODPS and ODCS documents (ADR-055, superseding ADR-032's
  descriptor format in part) and connectors land verbatim before bronze is published
  (ADR-036, revised).
- The decisions taken under the owner's delegation, and the owner's two rulings on FX content
  and landing retention, are listed in `docs/research/2026-11-26-decisions-addendum.md`.

### Added

- `k2gen release`: compiles a tag's documents into a sealed, read-only release named by the
  hash of its files and images, after the document, version, access and SQL checks pass;
  `k2gen diff` prints what deploying it would need.
- Emitters for Trino's configuration, rules and identity, the lake's DDL and reader views,
  the per-product pipelines, the dbt project and the catalog JSON.
- The first two products: an access canary that exercises every policy on the live stack,
  and the paused ClickHouse hot tier as a serving-only product.
- Principals and roles declared in `contracts/access/`; `make password`, `make api-key`,
  `make principal-remove` and `make principals-status`, with every secret generated under
  `${K2_SECRETS}` and never committed.
- `make tls-init`, `make tls-rotate` and `make tls-status` for the local CA and Trino's
  certificate.
- `make release`, `make deploy`, `make release-verify`, `make rollback`, `make release-status`,
  `make release-diff` and `make deploy-rehearsal`.
- The hand-written access suite (`tests/access/`, `make access-verify`, `make test-access`),
  the tested-data marker check and the catalog drift check.
- Alertmanager, delivering every alert to one Slack channel through a webhook kept as a local
  secret.
- `make spine-e2e`, the release and rules spine end to end from a scratch clone, run at merge
  points; `make catalog-fence`, which checks on the running stack that the catalog publishes
  no port, sits on its own network and shares it only with the listed services, and which
  every `release-verify` runs.
- CI compiles every pull request: `k2gen check`, a trial release and `k2gen diff` against the
  newest tag in the job summary, the emitted dbt trees, the rules suite and the access suite
  on a throwaway lake, and a test that holds the compose file to the catalog's allow-list.
- Architecture chapters 21 (releases and deploy) and 22 (access and identity); runbooks to
  deploy, roll back, verify access, manage TLS and passwords, and create or rotate a principal.

### Changed

- The stack runs from releases: services read their configuration through pointers under
  `${K2_RELEASES}`, not from the git checkout.
- Trino listens only on HTTPS and its Web UI is off; every client connects by name, verifies
  the local CA and logs in with its own password.

- The live stack was cut over to releases on 2026-11-27 (`docs/runbooks/first-cutover-to-releases.md`):
  the captures, Redpanda, MinIO and Postgres were not restarted.
- Generated files left git: `make generate`, `make new-product`, the old generator and
  validator, `docs/products/`, `docker/lake/generated/` and `docker/trino/etc/` are removed. A
  release is the only generated output.
- `lake-init` creates the warehouses of the release's storage plan.
- A deploy dumps a database before a one-shot migrates it, refuses when the image a rollback
  would need is gone, and rolls back with Trino down.

### Fixed

- A capture whose connect never returns is abandoned after a bound and retried, and a capture
  that delivers nothing for ten minutes exits to be restarted: on 2026-11-26 the Coinbase
  capture sat in its connect for 80 minutes with nothing logged.

- `scripts/principal-secret.py` no longer fails when two runs create the secrets directory at
  once.

### Security

- No credential, and no hash of one, is in git, a document or a release; a deploy record
  names each secret file the deploy used by presence, mode and size, never by content or
  digest.
- Readers cannot reach a dbt output's untested rows, its base table, its metadata tables or
  time travel; the catalog service has no host port and is reachable only from the
  platform's own services. Delay and row-scope policies suit trusted readers only: a delayed
  or scoped reader can infer value ranges of hidden rows (ADR-061, Outcome).
- The Docker socket is inside the catalog's trust boundary: the Airflow scheduler and cAdvisor
  hold it, and a network fence does not stop a service that can start containers (ADR-059,
  Outcome of 2026-11-27).
- The pinned MinIO image is no longer published by its vendor; the stack runs a mirrored copy
  pinned by digest. <!-- VERIFY: the mirror reference, once pushed -->

## [3.0.0-alpha.2] - 2026-11-26

Phase 000 of the unified product platform plan, "clear the ground": the old pipeline is
retired so the new platform is built once, on an empty lake. From this version until the
Kraken phase lands, no live crypto data reaches the lake; the captures keep writing to
Redpanda.

### Decided

- K2 will be rebuilt as a domain-agnostic data product platform: products defined once in
  contracts, one ingestion framework with connectors, dbt for silver and gold, one catalog,
  one enforcement point for SQL and REST (ADR-056, #245). The plan to the demo tag and to
  release (#246) supersedes phases P4 to P7 of the earlier plan; the target is drawn in
  architecture chapter 20 (#247) and the reasoning is kept in the research note (#244).
  Nothing in the running system changes with these documents.

- The ClickHouse hot tier is paused while the platform is built (ADR-057).
- ADR-048 and ADR-049, never built, are superseded by ADR-056.

### Added

- `make drop`: copies a lake table, namespace or bucket to the host, compares object count
  and bytes, and only then drops it; it refuses a bucket under object lock before copying
  (runbook `drop-and-rebuild.md`).
- Runbooks: pause and resume a serving product; upgrade a pinned component; drop and rebuild.
- The measured experiments the release spine depends on, as runnable scripts under
  `scripts/spikes/`, with their findings (`docs/research/2026-11-26-release-spine-spikes.md`)
  and the cost of one product run (`docs/benchmarks/2026-11-26-product-run-cost.md`).

### Changed

- Order-book capture is paused on every venue while the platform is built; trades are
  unaffected and the nine order-book tables keep their history (#232; ADR-050).

- The Spark image is built on the official Apache Spark 3.5.9 image, pinned by digest, and
  runs as a non-root user; the old base carried a Python and an OpenSSL past their end of
  life. Between jobs the container runs only the Spark History Server, on port 18080.
- ClickHouse starts only under the compose profile `hot-gold`; its volumes are kept.
- `make health` fails, where it used to warn, when a venue's capture is unhealthy or
  delivers nothing to Redpanda for two minutes.
- The declared budget at the bootstrap peak is 16.70 CPU / 28.688 GiB over 21 services
  (was 21.30 CPU / 37.812 GiB over 24).
- Gates that read the old tables print a SKIP line naming the phase that restores them:
  `lake-verify`, `parity-ohlcv`, `parity-bars`, `test-notebooks`, the lake chaos faults, the
  lake step of `make health`, the table copy of `make backup`, the dbt build of `make test-dbt`.

### Removed

- The v3 lake ingest: its Spark stages, the DAGs `lake_ingest`, `lake_maintenance`,
  `lake_rebuild` and `dbt_marts`, the dbt marts, the thirteen old-format product contracts,
  the notebooks, the lake exporter and its alerts and dashboard, and the services `lake-ddl`
  and `lake-metrics`. All of it is archived, unchanged, in `legacy/v3-lake-ingest/`, with a
  README that records the retirement.
- The data of the old pipeline: the `raw`, `bronze`, `silver`, `gold` and `audit` namespaces,
  the marts and the scratch namespaces, each dropped after a verified copy on the host. One
  scratch bucket under object lock is dropped when its lock expires.
- ClickHouse's alerts, scrape job, dashboard, chaos scripts and screenshots.

### Fixed

- Spark no longer shows the storage key in its job and history pages or keeps it in its event
  logs: the redaction pattern now covers the key names the lake catalog uses.
- A disk alert exists again: `DockerDiskUsageHigh` and `DockerDiskUsageCritical` read the
  volumes' disk from Redpanda, replacing the two rules that retired with the lake exporter.
- `make test` and `make gates` no longer rebuild the image tag the running Airflow services
  start from.
- Spark event logs are compressed and deleted after two days; they grew without bound in
  the Spark container.
- The ingest no longer fails when a batch holds no frame for a venue, which a paused
  order book produces on every run.

## [3.0.0-alpha.1] - 2026-11-26

First numbered version. It covers the work of phases P0 to P3 of the plan above, on top of the
v3 lake-first stack (tag `v3-phase-e`).

### Added

- Product descriptors and ODCS contracts for every lake table, with `k2gen validate`,
  `make generate` and a CI drift job (#183, #184; ADR-032, ADR-033).
- Airflow 3 runs the lake schedules, with the lake DAGs cut over from Prefect
  (#189, #195; ADR-034).
- Trino 484 over the lake through a read-only catalog, and one mart warehouse per data
  domain (#200; ADR-035, ADR-044).
- A dbt project and its first mart, `market_analytics.candles.ohlcv_1m`, with a
  `dbt_marts` DAG, delayed and scorecard marts, and mart maintenance (#201, #202).
- A governance domain, and one view of quality results across domains (#204; ADR-046).
- A batch audit: the ingest checks each batch after publishing it and never blocks the
  commit (#206; ADR-047).
- A nightly check that every live table matches its contract (#224).
- A nightly copy of the irreplaceable tables and the catalog database (#231).
- A per-venue order-book capture switch, all venues on (#230; ADR-050).
- cAdvisor, resource dashboards and reproducible screenshots (#194), and five guided
  lakehouse labs (#211).

### Changed

- The parity gate skips only a listed, recorded loss window, and a pin that cannot be
  resolved fails (#220, #226).
- Resource ceiling is 25 CPU / 48 GiB at the bootstrap peak, checked in CI
  (#180, #200; ADR-045).
- Readers resolve the snapshot `main` points at (#214).
- Branch protection on `main` requires CI to pass before merge.

### Fixed

- The ingest exits 75 when the writer lock is held, and one row per Binance book second
  survives across ingest runs (#181, #188).
- Ingest driver heap and offset cap raised for the bars recompute, with an alert on a
  sustained backlog (#203, #205).
- Guardrail gates that passed while violating what they guard (#228).
- A skipped dbt unit test is recorded on its model (#212).

### Removed

- The Prefect scheduler: its two services and the `legacy` compose profile are gone, and
  its code is archived in `legacy/prefect/` (#235; ADR-034 Outcome). There is no
  rollback to it in the compose file.

### Security

- Every published port binds to loopback, with a startup guard and a CI gate (#218).
- Every container log is capped (#229).
- Security bumps for the capture crates, notebooks and legacy v1 (#168, #177).

### Known limitations

- The restore from backup has not been drilled; a drill is planned for the hardening
  sprint (`docs/runbooks/lake-backup-and-restore.md`).
- The candle parity pin carries one recorded gap, a host out-of-memory kill, listed in
  `tests/parity/README.md` (ADR-029 Outcome).
- ADR-048 (the gold step reads only touched data and writes bars once) is Accepted and
  not built; ADR-049 (event bars extended, not recomputed) is Proposed.
- The plan's later phases (P4 to P7) are not done, so the contracts, marts and gates
  may still change.

[Unreleased]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.3...HEAD
[3.0.0-alpha.3]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.2...v3.0.0-alpha.3
[3.0.0-alpha.2]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.1...v3.0.0-alpha.2
[3.0.0-alpha.1]: https://github.com/rjdscott/k2-market-data-platform/releases/tag/v3.0.0-alpha.1
