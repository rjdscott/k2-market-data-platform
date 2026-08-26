# Testing

The test suite is deliberately small. It covers the pure logic that is easy to get
wrong and cheap to test — fixed-point conversion, book state, per-venue frame decoding,
config loading, offset bookkeeping, the Avro↔DDL contract — and leans on manual
failure-mode testing for everything that needs a live stack. Where that leaves gaps, they
are listed below rather than papered over.

```bash
make test          # everything: Python + Rust
make test-python   # contract, parity, lake-offset and wire-format tests (needs uv)
make test-rust     # capture unit + replay integration tests (runs in rust:1-bookworm)
```

## Inventory

| Suite | Count | Location | What it covers |
|-------|------:|----------|----------------|
| Rust — capture lib unit | 48 | [`services/capture-rust/src/`](../../services/capture-rust/src/) | `decimal`, `record`, `config`, `ws`, `book`, `sink`, `metrics`, `exchanges/{binance,kraken,coinbase}` — fixed-point conversion, book state, per-exchange framing |
| Rust — capture binary unit | 4 | [`services/capture-rust/src/main.rs`](../../services/capture-rust/src/main.rs) | The healthcheck's staleness parsing and per-stream bounds — the "green while the primary feed is dead" bug and its opposite |
| Rust — capture replay integration | 6 | [`services/capture-rust/tests/`](../../services/capture-rust/tests/) | `replay.rs` (2), `replay_binance.rs` (2), `replay_coinbase.rs` (2) — golden-fixture replay through the same `handle_frame()` path live capture runs |
| Python — v3 data contracts | 41 | [`tests/test_contracts.py`](../../tests/test_contracts.py) | Structural checks on `schemas/avro/*.avsc` and `config/instruments.yaml` — sibling `logicalType`, fixed-point prices, nullable defaults, duplicate/malformed canonical symbols |
| Python — parity comparator | 65 | [`tests/test_parity.py`](../../tests/test_parity.py) | `scripts/parity/compare_trades.py` — the tolerance boundary, Kraken's no-trade-id join, string → fixed-point conversion. This is the evidence the Kotlin retirement rested on ([ADR-019](../adr/ADR-019-rust-capture-tier.md)) |
| Python — lake offsets | 26 | [`tests/test_lake_offsets.py`](../../tests/test_lake_offsets.py) | `docker/lake/offsets.py`: the exactly-once bookkeeping written into the Iceberg snapshot summary — encode/decode round-trips, next-offset arithmetic, gap detection (ADR-022) |
| Python — wire format | 32 | [`tests/test_wire_format.py`](../../tests/test_wire_format.py) | `docker/lake/wire.py` plus the contract between `schemas/avro/*.avsc` and `docker/lake/ddl/lake.sql` — CLAUDE.md's schema-change rule, executable |
| **Total (Rust + Python)** | **222** | | |
| Archived v2 Kotlin | 20 | [`legacy/v2-kotlin/`](../../legacy/v2-kotlin/README.md) | `TradeNormalizerTest` (7), `InstrumentsLoaderTest` (13). `make test-legacy-kotlin` runs them; deliberately **not** part of `make test` and not run in CI |
| Legacy v1 | 180 | [`legacy/v1/tests/unit/`](../../legacy/v1/tests/unit/) | Archived. Kept for reference; not run in CI |

## Running them

### Rust

```bash
make test-rust      # cargo test --locked in rust:1-bookworm — 58 tests
```

No local toolchain needed. For a tight loop use the pre-built builder image and the named
cargo/target volumes described in
[`services/capture-rust/README.md`](../../services/capture-rust/README.md) — that is also
where `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings` are run the way
CI runs them.

### Python

```bash
uv run --no-project --with pytest --with pyyaml pytest tests -q
```

`--no-project` keeps the run isolated from the repo's own virtualenv, so the only
dependencies are pytest and PyYAML — every test is pure Python over files, with no Spark,
no catalog and no network. [`tests/conftest.py`](../../tests/conftest.py) injects
`docker/lake` onto `sys.path` — the `docker.` package name is already claimed by the Docker
SDK, so `offsets.py` and `wire.py` are imported by module name instead.

Lint the same code the way CI does:

```bash
uv run --no-project --with ruff ruff check docker/lake tests
```

## CI

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs six jobs on every PR and
on pushes to `main`: **rust**, **python**, **docker** (×3 matrix), **compose**, **docs**,
**security**. The **kotlin** job was deleted with the handlers — nothing in CI builds or
tests `legacy/v2-kotlin/`.

| Job | What it does |
|-----|--------------|
| **Rust (capture)** | `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo test --locked` on a pinned 1.98.0 toolchain — runs the 58 tests |
| **Python (lake + tests)** | `ruff check` then `pytest tests -q` under `uv` — runs the 164 tests |
| **Docker build** | 3-way matrix: Prefect worker, Spark, capture, with GHA layer caching. Every leg builds from the repo root. Catches broken build contexts, not runtime behaviour |
| **Compose (config validation)** | `docker compose --env-file .env.example config -q`, then a check that every service declares `deploy.resources.limits` — a service with no limit escapes the ADR-010 budget and nothing else would notice |
| **Docs** | `bash scripts/check-docs.sh` — link/word/rule gates, promtool, runbook annotation paths, capacity-model gate, mermaid width |
| **Security (Trivy)** | Filesystem scan for CRITICAL/HIGH findings, SARIF uploaded to GitHub code scanning. `legacy/` is skipped |

No job starts the stack, so nothing in CI exercises a real Redpanda, ClickHouse or
WebSocket connection.

## What is not covered

Stated plainly, because the gaps are structural rather than accidental:

- **The live socket** — `ws.rs` covers the backoff schedule and the `recv_ts_ns` unit, but
  nothing exercises a real connect, subscribe or mid-frame disconnect. Frame *decoding* is
  well covered, because `handle_frame` is pure and the replay fixtures drive it; the I/O
  around it is validated only by running against the live venue.
- **The produce path** — `sink.rs` has one test, that `warm_up()` fetches every subject a
  record can need. Avro encoding against a real registry and librdkafka delivery reports
  are not covered; that needs an embedded broker or a registry stub.
- **Every alert** — the 10 capture rules are evaluated against live series, but `make chaos`
  has never run, so no rule has been shown to fire on the fault it names and no capture
  recovery time is measured. Three of them carry `promtool` unit tests
  (`make check-alerts`) that pin the expression, not the recovery.
- **ClickHouse DDL and materialized views** — the bronze → silver → gold transform chain
  is SQL, and SQL is where the schema bugs live. Verified by hand with the queries in
  [../operations/data-inspection.md](../operations/data-inspection.md).
- **`docker/lake/ingest.py` and `maintenance.py`** — the Spark jobs themselves have no unit
  tests. Their pure helpers (`offsets.py`, `wire.py`) are covered, but the Kafka read, the
  Avro decode against a live registry and the Iceberg commit are only exercised against a
  running stack, by `make lake-verify` and the Phase D burn-in.
- **End-to-end** — no automated test asserts that a trade leaving an exchange arrives in
  an OHLCV candle. That path is validated manually, and by the latency measurements in
  [../operations/latency-budgets.md](../operations/latency-budgets.md).

## Manual failure-mode suite

Six infrastructure failure modes were induced and recovered on 2026-02-19 **against the v2
stack** — broker restart, database restart, feed-handler crash, offload failure,
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
  anything that needs a recorded session goes in `tests/` with its fixture.
- Python tests use plain pytest with `unittest.mock` — no fixtures framework, no plugins,
  and nothing that imports pyspark. Test the pure helper next to the Spark job rather than
  the job.
- Adding an exchange means at minimum a recorded fixture and a replay test for it — see
  [../operations/adding-new-exchanges.md](../operations/adding-new-exchanges.md).
- Follow the pragmatic TDD loop in [`CLAUDE.md`](../../CLAUDE.md): happy path first, then
  one meaningful edge case. A few good tests beat fifteen shallow ones.

## Related

- [setup.md](./setup.md) — getting a stack running before you test against it
- [../operations/data-inspection.md](../operations/data-inspection.md) — manual verification queries
