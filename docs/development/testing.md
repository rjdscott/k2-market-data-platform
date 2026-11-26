# Testing

The test suite is deliberately small. It covers the pure logic that is easy to get
wrong and cheap to test, fixed-point conversion, book state, per-venue frame decoding,
config loading, offset bookkeeping, the Avro↔DDL contract, and leans on manual
failure-mode testing for everything that needs a live stack. Where that leaves gaps, they
are listed below rather than papered over.

```bash
make test          # everything: Python + Rust
make test-python   # contract, parity, lake-offset and wire-format tests (needs uv)
make test-rust     # capture unit + replay integration tests (runs in rust:1.100.0-bookworm)
make test-trino    # Trino's etc/ starts; lake refuses every commit; only a domain's writer changes its catalog
make test-dbt      # dbt/ on a throwaway Trino: lint, compile, manifest check, build with its unit tests
make test-airflow  # the DAGs inside the Airflow image: lake.py's exit codes and kill path, dbt_marts as cosmos renders it
```

## Inventory

| Suite | Count | Location | What it covers |
|-------|------:|----------|----------------|
| Rust, capture lib unit | 52 | [`services/capture-rust/src/`](../../services/capture-rust/src/) | `decimal`, `record`, `config`, `ws`, `book`, `sink`, `metrics`, `exchanges/{binance,kraken,coinbase}`, fixed-point conversion, book state, per-exchange framing |
| Rust, capture binary unit | 4 | [`services/capture-rust/src/main.rs`](../../services/capture-rust/src/main.rs) | The healthcheck's staleness parsing and per-stream bounds, the "green while the primary feed is dead" bug and its opposite |
| Rust, capture replay integration | 12 | [`services/capture-rust/tests/`](../../services/capture-rust/tests/) | `replay.rs` (2), `replay_binance.rs` (2), `replay_coinbase.rs` (2), `replay_failures.rs` (3), `replay_cli.rs` (3), synthetic golden-fixture replay through the same `handle_frame()` path live capture runs; `make record-fixtures` runs the first three over real sessions recorded locally |
| Python, v3 data contracts | 41 | [`tests/test_contracts.py`](../../tests/test_contracts.py) | Structural checks on `schemas/avro/*.avsc` and `config/instruments.yaml`, sibling `logicalType`, fixed-point prices, nullable defaults, duplicate/malformed canonical symbols |
| Python, parity comparator | 69 | [`tests/test_parity.py`](../../tests/test_parity.py) | `scripts/parity/compare_trades.py`, the tolerance boundary, Kraken's no-trade-id join, string → fixed-point conversion. This is the evidence the Kotlin retirement rested on ([ADR-019](../adr/ADR-019-rust-capture-tier.md)) |
| Python, lake offsets | 60 | [`tests/test_lake_offsets.py`](../../tests/test_lake_offsets.py) | `docker/lake/offsets.py`: the exactly-once bookkeeping written into the Iceberg snapshot summary, encode/decode round-trips, next-offset arithmetic, gap detection (ADR-022) |
| Python, wire format | 35 | [`tests/test_wire_format.py`](../../tests/test_wire_format.py) | `docker/lake/wire.py` plus the contract between `schemas/avro/*.avsc` and `docker/lake/ddl/lake.sql`, CLAUDE.md's schema-change rule, executable |
| Python, everything else | 85 | [`tests/`](../../tests/) | bars, lake book/bronze/silver/SCD2/instruments/lock/maintenance/metrics, replay export, pinned notebooks, and `test_synthetic_fixtures.py` (the committed fixtures are what the generator writes) |
| **Total (Rust + Python)** | **358** | | Counted 2026-11-24: `cargo test` output and `pytest --collect-only -q` |
| Airflow DAGs and image | 29 | [`docker/airflow/tests/`](../../docker/airflow/tests/) | `lake.py` (17): schedules, exit 75 to skipped, params, the driver kill path as real processes; `dbt_marts.py` (10): one cosmos task group per selector, every dbt task as its domain's writer against `trino:8080`, a build task per model and a freshness task per source, the once-a-day maintenance gate, and governance built after every other domain however it ended (ADR-046); the image (2): every package in the dbt virtualenv at the version `dbt/uv.lock` pins, and the dbt packages installed from this `dbt/package-lock.yml`. Counted 2026-11-25 from `make test-airflow`'s `Ran 29 tests`. Not in the total |
| dbt, unit and data tests | 10 + 22 | [`dbt/models/`](../../dbt/README.md) | `ohlcv_1m`'s minute bucket and its tie-break on (`exchange_ts`, `recv_ts_ns`, `trade_seq`); the delayed view's 20-minute edge, its per-venue horizon and a stalled venue, with "now" fixed; the scorecard's UTC day, its scope-to-venue match, the day each audit is about and the losses by topic family; the quality-results view's status for each lake and dbt outcome; on made-up rows, run on Trino 484, each made to fail by breaking its model; every mart's grain uniqueness and not-null tests, the candle envelope, the delay itself, the scorecard's gap and loss counts, the four statuses of quality results, and the windowed uniqueness of `gold.trades` and `gold.ohlcv_1m`. Counted 2026-11-25 from `dbt build`'s summary. Not in the total, which counts Rust and Python |
| Archived v2 Kotlin | 20 | [`legacy/v2-kotlin/`](../../legacy/v2-kotlin/README.md) | `TradeNormalizerTest` (7), `InstrumentsLoaderTest` (13). `make test-legacy-kotlin` runs them; deliberately **not** part of `make test` and not run in CI |
| Legacy v1 | 180 | [`legacy/v1/tests/unit/`](../../legacy/v1/tests/unit/) | Archived. Kept for reference; not run in CI |

## Running them

### Rust

```bash
make test-rust      # cargo test --locked in rust:1.100.0-bookworm, 68 tests
```

No local toolchain needed. For a tight loop use the pre-built builder image and the named
cargo/target volumes described in
[`services/capture-rust/README.md`](../../services/capture-rust/README.md), that is also
where `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings` are run the way
CI runs them.

### Python

```bash
uv run --no-project --with pytest==9.2.0 --with pyyaml==6.0.4 --with fastavro==1.12.2 --with duckdb==1.4.4 pytest tests -q
```

`--no-project` keeps the run isolated from the repo's own virtualenv, so the only
dependencies are pytest, PyYAML, fastavro and DuckDB, every test is pure Python over
files, with no Spark, no catalog and no network. DuckDB (1.4.4, the notebooks' pin) runs
the Spark `MERGE` statements `docker/lake/books.py` builds, in `tests/test_lake_books.py`. [`tests/conftest.py`](../../tests/conftest.py) injects
`docker/lake` onto `sys.path`, the `docker.` package name is already claimed by the Docker
SDK, so `offsets.py` and `wire.py` are imported by module name instead.

Lint the same code the way CI does:

```bash
uv run --no-project --with ruff==0.17.6 ruff check docker/lake tests
```

## CI

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs ten jobs on every PR and
on pushes to `main`: **rust**, **python**, **contracts**, **clickhouse**, **trino**, **dbt**,
**docker** (a matrix), **compose**, **docs**, **security**. The **kotlin** job was deleted with the handlers, nothing in CI builds or
tests `legacy/v2-kotlin/`.

| Job | What it does |
|-----|--------------|
| **Rust (capture)** | `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked` on a pinned 1.98.0 toolchain, runs the 68 tests |
| **Python (lake + tests)** | `ruff check` then `pytest tests -q` under `uv`, runs the 299 tests |
| **Docker build** | matrix: Prefect worker, Airflow, lake-metrics, Spark, capture, with GHA layer caching. Every leg builds from the repo root. The Airflow leg then runs `make test-airflow`'s tests in the image it built, with `dbt/` mounted where the `dbt_marts` DAG reads it; the lake-metrics leg runs the exporter's self-check |
| **Trino (access rules)** | `bash scripts/trino-rules-test.sh` (`make test-trino`): the pinned `trinodb/trino` with `docker/trino/etc`, no network, two starts. The real `etc/`: a misspelt property fails the start, the catalogs must be `lake`, `system` and one per domain in `contracts/domains.yaml`, only `lake` is on warehouse `k2` and it is `read_only`. Then local Iceberg catalogs from the same generator: each domain's writer creates, inserts and drops in its own catalog; the next domain's writer and an unnamed user are denied every SQL write there and in `lake`; `optimize`, `rollback_to_snapshot`, `expire_snapshots`, `remove_orphan_files` and `CALL` through `lake` denied, and `CALL` through a domain catalog; nobody may set the two retention-floor session properties, so an expire at `0s` fails; a user sees only its own queries; the known table-procedure gap between domains asserted (ADR-044) |
| **dbt (marts)** | `bash scripts/dbt-test.sh` (`make test-dbt`): `sqlfluff lint` over `dbt/models`; the pinned `trinodb/trino` over the same local catalogs, with every lake table in `dbt/models/_sources.yml` created empty in its Trino types; `dbt deps`, `parse`, `compile --target trino`; `scripts/dbt_manifest_check.py` (only staging selects from a source, every model in a domain catalog, every mart declared in its contract and built as it says, a table unless `materialized: view`, reading exactly its declared tables, with a uniqueness test on its contract grain over the whole table at severity error); `dbt build --selector <domain>` as `dbt_<domain>`, which runs the unit tests, and every one of its tests and unit tests recorded in `<domain>.dq.dbt_test_results`; `dbt run-operation expire_mart_snapshots` as the same writer, which must reach every table mart of the domain and, after `optimize`, its test results; the same selector as another user, refused with `Access Denied`; a source test failed on purpose, the build non-zero and its failed and skipped tests recorded; the governance view, rebuilt, showing them and a failed and a raised lake check to a user who owns none of it; no writer inserting into another domain's results (ADR-046); `dbt run-operation create_test_results_table`, which `dbt_marts` runs first in each domain, making a results table on its own; and a project with no marts (k2gen's empty sources and selectors, no models) through `dbt parse` |
| **Compose (config validation)** | `docker compose --env-file .env.example config -q`, then `python3 scripts/check-budget.py`: prints the steady default, bootstrap default and bootstrap `--profile governance` and `--profile legacy` limit sums, each service's limits times its `deploy.replicas` (compose sets it from `scale` too), and fails when the bootstrap default is over 25 CPU / 48 GiB ([ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md)); fails any service under any profile (`--profile '*'`) without a CPU and memory limit (it would escape the budget and nothing else would notice), any image not pinned to a release (a digest, or a tag whose first number has a minor version: `rust:1-bookworm`, `postgres:15-alpine` and `nonroot` fail) in compose, a Dockerfile `FROM` or `COPY --from`, or a `docker run` in the Makefile or a script, any `pip install` or `uv run --with` without `==`, any GitHub Action not pinned by full commit SHA with its version in a comment, and any published port in any profile not bound to `127.0.0.1` |
| **Docs** | `bash scripts/check-docs.sh`, eleven failing gates: links, forbidden words (held as salted hashes in `scripts/check_docs.py`), promtool check and test, runbook annotation paths and presence, capacity-model gate, no plan checkboxes, no em-dash on lines added since `origin/main` (so the job checks out with full history; a push to main diffs from the commit before the push), every data file generated at its hash in `tests/fixtures/SHA256SUMS` or listed in `NOTICE` and no notebook with outputs, no count of captured data in an editable Markdown file. Mermaid width only warns |
| **Security (Trivy)** | Filesystem scan for CRITICAL/HIGH findings, SARIF uploaded to GitHub code scanning. `legacy/` is skipped |

No job starts the stack, so nothing in CI exercises a real Redpanda, ClickHouse or
WebSocket connection.

## What is not covered

Stated plainly, because the gaps are structural rather than accidental:

- **The live socket**, `ws.rs` covers the backoff schedule and the `recv_ts_ns` unit, but
  nothing exercises a real connect, subscribe or mid-frame disconnect. Frame *decoding* is
  well covered, because `handle_frame` is pure and the replay fixtures drive it; the I/O
  around it is validated only by running against the live venue.
- **The produce path**, `sink.rs` has one test, that `warm_up()` fetches every subject a
  record can need. Avro encoding against a real registry and librdkafka delivery reports
  are not covered; that needs an embedded broker or a registry stub.
- **Every alert**, the 10 capture rules are evaluated against live series, but `make chaos`
  has never run, so no rule has been shown to fire on the fault it names and no capture
  recovery time is measured. Three of them carry `promtool` unit tests
  (`make check-alerts`) that pin the expression, not the recovery.
- **ClickHouse DDL and materialized views**, the bronze → silver → gold transform chain
  is SQL, and SQL is where the schema bugs live. Verified by hand with the queries in
  [../operations/data-inspection.md](../operations/data-inspection.md).
- **`docker/lake/ingest.py` and `maintenance.py`**, the Spark jobs themselves have no unit
  tests. Their pure helpers (`offsets.py`, `wire.py`) are covered, but the Kafka read, the
  Avro decode against a live registry and the Iceberg commit are only exercised against a
  running stack, by `make lake-verify` and the Phase D burn-in.
- **End-to-end**, no automated test asserts that a trade leaving an exchange arrives in
  an OHLCV candle. That path is validated manually, and by the latency measurements in
  [../operations/latency-budgets.md](../operations/latency-budgets.md).

## Manual failure-mode suite

Six infrastructure failure modes were induced and recovered on 2026-02-19 **against the v2
stack**, broker restart, database restart, feed-handler crash, offload failure,
object-store outage and network partition. All six passed, worst MTTR 32 seconds. Those
numbers belong to the Kotlin tier and have not been re-measured since it retired, and two
of them were induced against the v2 offload, which is deleted; the runbook now points those
at the v3 lake path and marks them unmeasured until the Phase D burn-in. The capture-tier
equivalent is `make chaos`. The procedures are written up as a runbook so they double as
incident response:

**[../runbooks/failure-recovery.md](../runbooks/failure-recovery.md)**

Re-run them after any change to the compose topology, healthchecks or consumer
configuration. Each mode is one command to induce and one to recover.

## Adding tests

- Rust tests live in a `#[cfg(test)] mod tests` at the foot of the module they cover;
  anything that replays a session goes in `tests/` with its synthetic fixture.
- Python tests use plain pytest with `unittest.mock`, no fixtures framework, no plugins,
  and nothing that imports pyspark. Test the pure helper next to the Spark job rather than
  the job.
- Adding an exchange means at minimum a synthetic fixture and a replay test for it, see
  [../operations/adding-new-exchanges.md](../operations/adding-new-exchanges.md).
- Follow the pragmatic TDD loop in [`CLAUDE.md`](../../CLAUDE.md): happy path first, then
  one meaningful edge case. A few good tests beat fifteen shallow ones.

## Related

- [setup.md](./setup.md), getting a stack running before you test against it
- [../operations/data-inspection.md](../operations/data-inspection.md), manual verification queries
