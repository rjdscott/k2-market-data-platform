# Testing

The v2 test suite is deliberately small. It covers the pure logic that is easy to get
wrong and cheap to test — symbol normalisation, config loading, orchestration control
flow — and leans on manual failure-mode testing for everything that needs a live stack.
Where that leaves gaps, they are listed below rather than papered over.

```bash
make test          # everything: Kotlin + Python
make test-kotlin   # feed handler unit tests
make test-python   # offload flow unit tests (needs uv)
```

## Inventory

| Suite | Count | Location | What it covers |
|-------|------:|----------|----------------|
| Kotlin — `TradeNormalizerTest` | 7 | [`services/feed-handler-kotlin/src/test/kotlin/com/k2/feedhandler/TradeNormalizerTest.kt`](../../services/feed-handler-kotlin/src/test/kotlin/com/k2/feedhandler/TradeNormalizerTest.kt) | Per-exchange symbol normalisation and trade mapping to the canonical schema |
| Kotlin — `InstrumentsLoaderTest` | 9 | [`InstrumentsLoaderTest.kt`](../../services/feed-handler-kotlin/src/test/kotlin/com/k2/feedhandler/InstrumentsLoaderTest.kt) | Parsing `config/instruments.yaml`, per-exchange lookup, missing-file and malformed-YAML handling |
| Python — Iceberg maintenance flow | 28 | [`tests/test_iceberg_maintenance_flow.py`](../../tests/test_iceberg_maintenance_flow.py) | Compact / expire / audit tasks, the parent flows, failure policy, and script helpers — all with the Spark subprocess mocked |
| **v2 total** | **44** | | |
| Legacy v1 | 180 | [`legacy/v1/tests/unit/`](../../legacy/v1/tests/unit/) | Archived. Kept for reference; not run in CI |

## Running them

### Kotlin

```bash
cd services/feed-handler-kotlin
./gradlew test --no-daemon          # 16 tests
./gradlew build --no-daemon         # compile + test, what CI runs
```

Needs JDK 21 (the build sets `jvmToolchain(21)`). No local JDK? Run it in the same image
CI uses:

```bash
docker run --rm -v "$PWD":/project -w /project/services/feed-handler-kotlin \
  -e GRADLE_USER_HOME=/tmp/.gradle gradle:8.12-jdk21 ./gradlew test --no-daemon
```

HTML report: `services/feed-handler-kotlin/build/reports/tests/test/index.html`.
JUnit XML: `build/test-results/test/`.

### Python

```bash
uv run --no-project --with prefect --with psycopg2-binary --with pytest pytest tests -q
```

`--no-project` keeps the run isolated from the repo's own virtualenv, so the only
dependencies are Prefect and pytest. [`tests/conftest.py`](../../tests/conftest.py) injects
`docker/offload` and `docker/offload/flows` onto `sys.path` — the `docker.` package name is
already claimed by the Docker SDK, so the flows are imported by module name instead.

Lint the same code the way CI does:

```bash
uv run --no-project --with ruff ruff check docker/offload tests
```

## CI

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) runs four jobs on every PR and
on pushes to `main`:

| Job | What it does |
|-----|--------------|
| **Kotlin (feed handler)** | `./gradlew build` on JDK 21 — compiles and runs the 16 tests. Uploads the HTML test report on failure |
| **Python (offload + tests)** | `ruff check` then `pytest tests -q` under `uv` |
| **Docker build** | Builds all three Dockerfiles (feed handler, Prefect worker, Spark) with GHA layer caching. Catches broken build contexts, not runtime behaviour |
| **Security (Trivy)** | Filesystem scan for CRITICAL/HIGH findings, SARIF uploaded to GitHub code scanning. `legacy/` is skipped |

No job starts the stack, so nothing in CI exercises a real Redpanda, ClickHouse or
WebSocket connection.

## What is not covered

Stated plainly, because the gaps are structural rather than accidental:

- **WebSocket clients** — `BinanceWebSocketClient`, `KrakenWebSocketClient` and
  `CoinbaseWebSocketClient` have no tests. Connection handling, subscription framing,
  reconnect-with-backoff and per-exchange message parsing are all validated only by
  running against the live exchange. This is the largest gap; a fake WebSocket server
  fed with captured frames would close most of it.
- **`KafkaProducerService`** — Avro record construction, the dual raw/normalized produce
  path and the Micrometer counters are untested. It needs an embedded broker or a
  `MockProducer`.
- **ClickHouse DDL and materialized views** — the bronze → silver → gold transform chain
  is SQL, and SQL is where the schema bugs live. Verified by hand with the queries in
  [../operations/data-inspection.md](../operations/data-inspection.md).
- **`offload_generic.py`** — the Spark job itself is only covered indirectly. The Prefect
  flow around it is tested with the subprocess mocked, which validates orchestration but
  not the JDBC read or the Iceberg append.
- **End-to-end** — no automated test asserts that a trade leaving an exchange arrives in
  an OHLCV candle. That path is validated manually, and by the latency measurements in
  [../operations/latency-budgets.md](../operations/latency-budgets.md).

## Manual failure-mode suite

Six infrastructure failure modes were induced and recovered on 2026-02-19 — broker
restart, database restart, feed-handler crash, offload failure, object-store outage and
network partition. All six passed, worst MTTR 32 seconds. The procedures are written up as
a runbook so they double as incident response:

**[../runbooks/failure-recovery.md](../runbooks/failure-recovery.md)**

Re-run them after any change to the compose topology, healthchecks or consumer
configuration. Each mode is one command to induce and one to recover.

## Adding tests

- Kotlin tests use `kotlin.test` with JUnit Platform; put them alongside the existing two
  files in `src/test/kotlin/com/k2/feedhandler/`.
- Python tests use plain pytest with `unittest.mock` — no fixtures framework, no plugins.
  Mock the subprocess boundary rather than Spark itself.
- Adding an exchange means at minimum a new `TradeNormalizerTest` case for its symbol
  format — see [../operations/adding-new-exchanges.md](../operations/adding-new-exchanges.md).
- Follow the pragmatic TDD loop in [`CLAUDE.md`](../../CLAUDE.md): happy path first, then
  one meaningful edge case. A few good tests beat fifteen shallow ones.

## Related

- [setup.md](./setup.md) — getting a stack running before you test against it
- [../operations/data-inspection.md](../operations/data-inspection.md) — manual verification queries
