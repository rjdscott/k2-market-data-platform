# Testing

The test suite is deliberately small. It covers the pure logic that is easy to get
wrong and cheap to test, fixed-point conversion, book state, per-venue frame decoding,
config loading, offset bookkeeping, the Avro↔ClickHouse contract, and leans on manual
failure-mode testing for everything that needs a live stack. Where that leaves gaps, they
are listed below rather than papered over.

```bash
make test             # every suite below but spine-e2e: python, rust, clickhouse, trino, dbt, notebooks, k2gen, airflow, connectors, api, platform
make test-python      # contract, lake-offset, wire-format, bars, the connector's and the scripts' tests (needs uv)
make test-rust        # capture unit + replay integration tests (runs in rust:1.100.0-bookworm)
make test-trino       # a compiled release's rules on a throwaway Trino and Lakekeeper: default deny, exact names, writer fenced from writer
make test-dbt         # lint, parse, compile, the manifest check, then each compiled product built with its unit tests on a throwaway lake (Docker, about 75 s)
make test-airflow     # inside the Airflow image: the DAG factory, the one-shot operator, every dbt package at the version dbt/ pins
make test-connectors  # the fetch container's address guard and run contract (docker/fetch/fetch.py), every lookup and socket faked
make test-api         # k2-api's tests and ruff from its own lock (services/k2-api/); a fake at its one Trino client
make test-platform    # the run-records client, the catalog site generator and the dbt wrapper: fakes only, no stack
make spine-e2e        # the release and rules spine end to end on throwaway containers: run at merge points, not per step
```

`make spine-e2e` is the project's merge-point practice and has [a page of its own](spine-e2e.md): its five stages, how to read a failure, and why it is not in CI.

## Inventory

Counted on 2026-11-26, after phase 000 moved the v3 lake's suites, the parity comparator's among
them, to [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/): the Python counts are what
`pytest --collect-only -q tests` prints per file (499 in all), the Rust counts are `#[test]`
functions per file.

| Suite | Count | Location | What it covers |
|-------|------:|----------|----------------|
| Rust, capture lib unit | 54 | [`services/capture-rust/src/`](../../services/capture-rust/src/) | `decimal`, `record`, `config`, `ws`, `book`, `resync`, `sink`, `metrics`, `exchanges/{binance,kraken,coinbase}`, fixed-point conversion, book state, per-exchange framing |
| Rust, capture binary unit | 9 | [`services/capture-rust/src/main.rs`](../../services/capture-rust/src/main.rs) | The healthcheck's staleness parsing and per-stream bounds, the "green while the primary feed is dead" bug and its opposite; the book switch's streams and series, Binance's ping and pong watchdog, the scheduled reconnect |
| Rust, book switch integration | 4 | [`services/capture-rust/tests/book_switch.rs`](../../services/capture-rust/tests/book_switch.rs) | The `book_capture` switch through the public API ([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)): each venue's subscriptions with it off and on, and every synthetic fixture replaying to its golden hash either way |
| Rust, capture replay integration | 12 | [`services/capture-rust/tests/`](../../services/capture-rust/tests/) | `replay.rs` (2), `replay_binance.rs` (2), `replay_coinbase.rs` (2), `replay_failures.rs` (3), `replay_cli.rs` (3), synthetic golden-fixture replay through the same `handle_frame()` path live capture runs; `make record-fixtures` runs the first three over real sessions recorded locally |
| Python, v3 data contracts | 41 | [`tests/test_contracts.py`](../../tests/test_contracts.py) | Structural checks on `schemas/avro/*.avsc` and `config/instruments.yaml`, sibling `logicalType`, fixed-point prices, nullable defaults, duplicate/malformed canonical symbols |
| Python, lake offsets | 66 | [`tests/test_lake_offsets.py`](../../tests/test_lake_offsets.py) | `docker/lake/offsets.py`: the exactly-once bookkeeping written into the Iceberg snapshot summary, encode/decode round-trips, next-offset arithmetic, gap detection (ADR-022) |
| Python, wire format | 20 | [`tests/test_wire_format.py`](../../tests/test_wire_format.py) | `docker/lake/wire.py`'s framing, and the contract between `schemas/avro/*.avsc` and the ClickHouse DDL, CLAUDE.md's schema-change rule, executable |
| Python, everything else | 372 | [`tests/`](../../tests/) | bars, the lake's book, instruments and empty batch, the gate scripts (`check-budget`, `check-docs`, `check-env`, `health`, `drop`, `backup-lake`), and `test_synthetic_fixtures.py` (the committed fixtures are what the generator writes) |
| Airflow image | 2 | [`docker/airflow/tests/`](../../docker/airflow/tests/) | every package in the dbt virtualenv at the version `dbt/uv.lock` pins, and the dbt packages installed from this `dbt/package-lock.yml`. Not in the Rust and Python counts. Since 2026-11-27 also `test_factory.py` and `test_oneshot.py`, the DAG factory and the one-shot operator, not counted here |
| dbt | none on 2026-11-26 | [`dbt/`](../../dbt/) | No models then: the v3 marts and their unit and data tests are archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/). Each product's models and unit tests now live in its contract, `contracts/<domain>/<product>/models/` and `unit_tests.yml`, and `make test-dbt` builds them on a throwaway lake |
| Archived v2 Kotlin | 20 | [`legacy/v2-kotlin/`](../../legacy/v2-kotlin/README.md) | `TradeNormalizerTest` (7), `InstrumentsLoaderTest` (13). `make test-legacy-kotlin` runs them; deliberately **not** part of `make test` and not run in CI |
| Legacy v1 | 180 | [`legacy/v1/tests/unit/`](../../legacy/v1/tests/unit/) | Archived. Kept for reference; not run in CI |

Suites added on 2026-11-27, not in the 499, counted the same way (`pytest --collect-only -q
<path>`, its last line):

| Suite | Count | Run by | What it covers |
|-------|------:|--------|----------------|
| The fetch container | 74 | `make test-connectors` ([`tests/test_fetch.py`](../../tests/test_fetch.py)) | `docker/fetch/fetch.py`: the address guard (https on 443, `allowHosts`, public unicast only, at most five redirects followed by hand, the 64 MiB cap, the deadline) and the run contract's exits 0, 10, 20, 30 and 1, with every lookup and socket faked |
| k2-api | 153 | `make test-api` ([`services/k2-api/tests/`](../../services/k2-api/tests/)) | the routes, keys and status codes against a fake at the one Trino client that enforces the compiled rules of a release |
| Run records and catalog site | 40 | `make test-platform` ([`platform/runs/`](../../platform/runs/), [`platform/catalog-site/`](../../platform/catalog-site/)) | the one client every writer of the run records uses; the catalog site's pages, escaping and atomic publish |
| The dbt wrapper | 79 | `make test-platform` ([`docker/dbt-runner/`](../../docker/dbt-runner/)) | `k2_dbt.py`'s order (orphans, reconcile, resolve, build, restore, publish) and exit codes 0 to 4 against fakes |
| Parsers | 53 | CI's python job (`pytest domains -q`), not `make test` | each product's registered parser and its audits under `domains/<domain>/parsers/`, over synthetic fixtures |

## Proofs on throwaway containers

Each is a script under [`scripts/verify/`](../../scripts/verify/) that proves one part of the run
path on containers of its own, never the stack, and prints one `PASS` or `FAIL` line per claim
and a total; exit 0 only when none failed. They need Docker and are not in `make test` or CI: run
the one whose part a change touches.

| Script | What it proves |
|--------|----------------|
| `storage-keys.sh` | on a lake made by the real `docker/lake/init-lake.sh`: one MinIO user per engine with only its own policy, every warehouse unsigned, a rerun changes nothing, Spark writes warehouse `k2` on its key, the maintenance key expires and cannot write, every storage-key row of the access suite, Trino on its scoped key |
| `oneshot-slots.sh` | the one-shot operator on a throwaway Airflow: four LocalExecutor slots and the pools `lake_spark` 1, `fetch` 1, `dbt` 2 hold; a killed task leaves no container; fetch sees its own DAG's spool directory alone and runs on `k2-egress`, where the project's database does not resolve |
| `dbt-wrapper.sh` | the dbt wrapper in its own image, as the operator runs it, on a throwaway lake: a pass moves the tested tag, a failed test rolls back while a reader polling every 50 ms never sees a failed row, a killed run is reconciled, a changed or retyped column |
| `catalog-site.sh` | offline: a trial release's catalog site, a page per product, no script, no outside link, no secret-looking text, and a planted secret fails the check; with a release and a site directory, checks that site |
| `api-schema.sh` | every catalog document k2-api serves validates against the frozen schema, and `/v1/openapi.json` declares OpenAPI 3.1.0; `--release <dir>` checks a release with no k2-api |

## Running them

### Rust

```bash
make test-rust      # cargo test --locked in rust:1.100.0-bookworm, 79 tests
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
files, with no Spark, no catalog and no network. DuckDB (1.4.4) runs the bar SQL
`docker/lake/bars.py` builds, in `tests/test_bars.py`. [`tests/conftest.py`](../../tests/conftest.py) injects
`docker/lake` onto `sys.path`, the `docker.` package name is already claimed by the Docker
SDK, so `offsets.py` and `wire.py` are imported by module name instead.

Lint the same code the way CI does:

```bash
uv run --no-project --with ruff==0.17.6 ruff check docker/lake tests
```

## CI

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs eleven jobs on every PR and
on pushes to `main`: **rust**, **python**, **contracts**, **api**, **clickhouse**, **trino**, **dbt**,
**docker** (a matrix), **compose**, **docs**, **security**. The **kotlin** job was deleted with the handlers, nothing in CI builds or
tests `legacy/v2-kotlin/`.

| Job | What it does |
|-----|--------------|
| **Rust (capture)** | `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked` on a pinned 1.98.0 toolchain, runs the 79 tests |
| **Python (lake + tests)** | `make lint`, `pytest tests -q` under `uv`, then `make test-platform` and the parsers' `pytest domains -q` |
| **k2-api (tests)** | `make test-api`: its pytest and ruff from `services/k2-api/uv.lock`, no network and no Trino |
| **Docker build** | matrix: Airflow, lake-metrics, Spark, capture, fetch, dbt-runner, tools, k2-api, with GHA layer caching; each image then runs one check of itself (the DAG tests in Airflow's, the fetch command, dbt and the wrapper's command, k2gen in tools', k2-api's `/healthz`). Every leg builds from the repo root. The Airflow leg then runs `make test-airflow`'s tests in the image it built, with `dbt/` mounted; the lake-metrics leg builds the retired exporter's Dockerfile at its archived path in `legacy/v3-lake-ingest/`, so the archive stays buildable and the leg's required name keeps reporting |
| **Trino (access rules)** | `bash scripts/trino-rules-test.sh` (`make test-trino`): k2gen compiles the good fixture set plus a same-domain twin product into a release, and `scripts/lib/throwaway-lake.sh` starts Trino 484 from that release's own `etc`, `rules.json` and `groups.txt` over a throwaway Lakekeeper, Postgres and MinIO on a network of their own, every principal logging in with a per-run password over HTTPS. The suite (`scripts/trino_rules_test.py`) is a table of named rows: each role reads exactly its policy's rows and masks and never the untested row; every base path, both catalogs, refused to every reader and stranger; views, listings, builders and publishers; every session property; `system.runtime` and query history the operator's; impersonation; the release canary; the set's own expectation rows; spike (e)'s 40 statements, writer against writer. A negative control widens one rule in place and the suite must fail; a mask or filter on a column the view lacks fails closed ([ADR-059](../adr/ADR-059-access-rules-compiled-default-deny-over-two-catalogs-per-domain.md)) |
| **dbt (marts)** | `bash scripts/dbt-test.sh` (`make test-dbt`): `sqlfluff lint`; `dbt deps`, `parse` and `compile --target trino` and the manifest check; `k2_input`'s render cases; then the real contracts compiled into a trial release and every product built by one `dbt build` with its unit tests on a throwaway lake, as its builder over HTTPS, in the order its input ports give, and the results hook's table read back against `run_results.json`. The name is kept because branch protection requires it |
| **Compose (config validation)** | `docker compose --env-file .env.example config -q`, then `python3 scripts/check-budget.py`: prints the steady default, the bootstrap default and, for each profile a service names (`hot-gold` today), the default plus that profile: limit sums, each service's limits times its `deploy.replicas` (compose sets it from `scale` too), and fails when the bootstrap default is over 25 CPU / 48 GiB ([ADR-045](../adr/ADR-045-ceiling-25-cpu-48-gib.md)); fails any service under any profile (`--profile '*'`) without a CPU and memory limit (it would escape the budget and nothing else would notice), any image not pinned to a release (a digest, or a tag whose first number has a minor version: `rust:1-bookworm`, `postgres:15-alpine` and `nonroot` fail) in compose, a Dockerfile `FROM` or `COPY --from`, or a `docker run` in the Makefile or a script, any `pip install` or `uv run --with` without `==`, any GitHub Action not pinned by full commit SHA with its version in a comment, and any published port in any profile not bound to `127.0.0.1` |
| **Docs** | `bash scripts/check-docs.sh`, eleven failing gates: links, forbidden words (held as salted hashes in `scripts/check_docs.py`), promtool check and test, runbook annotation paths and presence, capacity-model gate, no plan checkboxes, no em-dash on lines added since `origin/main` (so the job checks out with full history; a push to main diffs from the commit before the push), every data file generated at its hash in `tests/fixtures/SHA256SUMS` or listed in `NOTICE` and no notebook with outputs, no count of captured data in an editable Markdown file. Mermaid width only warns |
| **Security (Trivy)** | Filesystem scan of the lockfiles for CRITICAL/HIGH findings, SARIF uploaded to GitHub code scanning; then the gate: a CRITICAL with a fix available fails the job unless `.trivyignore.yaml` lists it with a reason and an expiry date (`tests/test_ci_security.py`). `legacy/` is skipped; the third-party compose images are not scanned |

No job starts the stack, so nothing in CI exercises a real Redpanda, ClickHouse or
WebSocket connection. `make test-access` and the deploy rehearsal are not in CI either: they run in
[`make spine-e2e`](spine-e2e.md), at merge points.

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
- **The ClickHouse feeds**, `make test-clickhouse` applies the `gold` DDL to a throwaway
  server and asserts on it, but the Kafka-engine feeds never read a real broker in a test.
  The hot tier is paused (ADR-057).
- **The lake's writes**, the file connector's load, audit and publish (`docker/lake/connector.py`)
  are covered as pure flows over a fake store and lake (`tests/test_lake_connector.py`), and a real
  Spark write by `storage-keys.sh`; no automated test runs a product's whole DAG on a lake (fetch
  to gold). The kept helpers (`offsets.py`, `wire.py`, `bars.py`, `books.py`) are covered as pure
  code; the Kafka read returns with phase 004's connector and its tests. The v3 ingest's jobs and
  their tests are archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/).
- **End-to-end**, no automated test asserts that a trade leaving an exchange arrives in
  a product: nothing carries it past Redpanda until phase 004
  ([../operations/latency-budgets.md](../operations/latency-budgets.md)).

## Manual failure-mode suite

Six infrastructure failure modes were induced and recovered on 2026-02-19 **against the v2
stack**, broker restart, database restart, feed-handler crash, offload failure,
object-store outage and network partition. All six passed, worst MTTR 32 seconds. Those
numbers belong to the Kotlin tier and have not been re-measured since it retired, and two
of them were induced against the v2 offload, which is deleted; of those, the batch-job failure
retired with the v3 lake too, and MinIO's lake-side recovery is unmeasured until phase 004.
The capture-tier
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
