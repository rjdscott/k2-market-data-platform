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

[Unreleased]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.2...HEAD
[3.0.0-alpha.2]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.1...v3.0.0-alpha.2
[3.0.0-alpha.1]: https://github.com/rjdscott/k2-market-data-platform/releases/tag/v3.0.0-alpha.1
