# Changelog

All notable changes to this platform are recorded here, in the
[Keep a Changelog 1.1](https://keepachangelog.com/en/1.1.0/) format.

A version names one tested set of everything in this repository: capture, the lake,
Trino, dbt, Airflow, the contracts and the gates, under one
[SemVer](https://semver.org/) number. An alpha may change anything, including table
layouts and commands, without notice. `3.0.0` is reserved for the end of
[the governed self-serve platform plan](docs/plans/2026-11-24-governed-self-serve-platform/README.md)
(phase P7).

## [Unreleased]

### Changed

- Order-book capture is paused on every venue while the platform is built; trades are
  unaffected and the nine order-book tables keep their history (#232; ADR-050).

### Fixed

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

[Unreleased]: https://github.com/rjdscott/k2-market-data-platform/compare/v3.0.0-alpha.1...HEAD
[3.0.0-alpha.1]: https://github.com/rjdscott/k2-market-data-platform/releases/tag/v3.0.0-alpha.1
