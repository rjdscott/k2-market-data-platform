# Runbook: Bring the stack up from a fresh clone

Zero images, zero volumes, to a running stack: the captures writing to Redpanda, an empty
lake catalog, and Airflow with no DAG. The v3 lake ingest pipeline that filled every lake layer
within 15 minutes of a fresh install is retired and archived in
[`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/)
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)); data reaches the lake again from
[phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan. Use this for a first install, after a Docker wipe, or as the
release gate's manual twin. It does not cover upgrading a stack that already holds data, nor
restoring from the host copy ([lake-backup-and-restore.md](./lake-backup-and-restore.md)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Capture image build race aborts `docker compose up` | < 5 min | ~1 min re-run, 2026-08-28 (fixed in tree) |

---

## Prerequisites

- A Docker engine with **at least 29 GiB of memory** so every declared limit is honoured,
  including the 28.688 GiB bootstrap peak while the one-shots run
  ([docker-resources.md](../operations/docker-resources.md), which says how the figure was
  computed). ClickHouse is paused behind the
  compose profile `hot-gold` ([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)) and a fresh install does not start it; with
  that profile running the peak is 36.688 GiB, so 37 GiB.
  Measured usage on a fresh install is far lower (~4.5 GiB RSS total; largest single
  container ClickHouse at 1.13 GiB, on 2026-08-28, when it still started).
- **Any core count.** `.env.example` ships `K2_CAPTURE_CPUSET=` and `K2_HEAVY_CPUSET=`
  empty, which is no CPU pinning and starts on every host. The 15-core layout (capture on
  `12-14`, the heavy tier ClickHouse/Spark/Trino on `0-11`) is commented out beside
  them; uncomment it only on a host that has those cores, because a cpuset naming a core
  the host does not have fails the container at start with `invalid argument`. The compose
  default uses `-` and not `:-`, so an explicit empty value wins over `12-14`.
  See [`.env.example`](../../.env.example) for the full note.
- **The published ports free on `127.0.0.1`**: 3000, 4040, 8080, 8081, 8088, 8090, 8123,
  9000, 9001, 9002, 9090, 9092, 9363, 9644, 15432, 18080, 18181 and 19092 (the `ports:` of
  `docker-compose.yml`). `K2_AIRFLOW_PORT` (8090) and `K2_TRINO_PORT` (8088) in `.env` move
  two of them; `K2_AIRFLOW_PORT` also moves the base URL Airflow's UI builds its links from,
  so change the variable, not the mapping alone.
- **On Docker Desktop, a host with RAM to spare beyond the VM.** The VM grows to its whole
  memory setting within hours, so host RAM must exceed that setting plus everything else the
  host runs; on 2026-11-25 the host killed a 51,712 MiB VM on a desktop with 62.6 GiB of usable RAM
  ([docker-vm-oom-kill.md](./docker-vm-oom-kill.md#2-size-the-vm-so-the-host-keeps-enough-memory)).
- **Every `change-me-in-production` value in `.env` replaced**, and `MINIO_ROOT_PASSWORD`
  different from `MINIO_ROOT_USER`. `make up` stops before compose with
  `check-env: FAIL .env: <NAME> ...` lines naming each variable left, never its value
  ([SECURITY.md](../../SECURITY.md)). The [operator guide](../operations/README.md#1-start-from-a-fresh-clone)
  has a command that fills every one.
- **Roughly 4 GB of image space** for the three built images: `k2-capture` 54.1 MB,
  `k2-airflow` 1.72 GB and `k2-spark-iceberg` 2.06 GB (`docker images`, 2026-11-26; the Spark
  image was 6.47 GB on its old base), on top of the upstream pulls (~5.3 GB on 2026-08-28,
  before Airflow, cAdvisor and Trino joined).

## Command sequence

```bash
git clone https://github.com/rjdscott/k2-market-data-platform.git
cd k2-market-data-platform
cp .env.example .env            # set every change-me value: openssl rand -hex 24 each;
                                # LAKEKEEPER_ENCRYPTION_KEY: openssl rand -base64 32
set -a && . ./.env && set +a
make up                         # scripts/check-env.py, then docker compose up -d;
                                # first run pulls the upstream images and builds 3
make health                     # every service healthy, every venue delivering to Redpanda in the last 2 min
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance --num 1
```

The v3 lake's own check, `lake-verify.sh`, retired with it: `make lake-verify` prints a SKIP line
naming phase 004, where the new platform's product checks return.

Then the two binding checks in [SECURITY.md](../../SECURITY.md#check-the-binding): check 1
prints `PASS` (no compose port on `*:` or `0.0.0.0:`) and check 2 prints `loopback 200`,
`lan 000`. Not yet run on a fresh clone. <!-- VERIFY -->

## Timeline

Measured 2026-08-28 on a 28-core host, 40 GB Docker VM, 648 GB free, fast network, and
**not re-measured since 2026-08-28**: the stack then had 19 containers and no Airflow,
cAdvisor or Trino, and the lake was scheduled by the scheduler Airflow replaced
([`legacy/prefect/`](../../legacy/prefect/README.md)). Elapsed is from the start of the
first `docker compose up -d`. ClickHouse, in two rows below, is paused since (ADR-057): a fresh
install today does not start it. The lake rows describe the v3 lake ingest pipeline, its exporter
and its one-shot, all retired since: a fresh install today stops at an empty lake.

| Elapsed | What happens | Duration |
|---|---|---|
| 0 s | `docker compose up -d`, no output while it pulls and builds | — |
| +83 s | 9 upstream images pulled, ~5.3 GB | 83 s |
| +83 s → +10 m | Builds: `k2-capture`, the retired scheduler's worker image, `k2-spark-iceberg` | ~9.5 min, dominated by the 6.47 GB Spark image |
| +10 m | All 19 containers created from cached images | 14 s wall |
| +10 m 14 s | `lake-metrics` logs 15× `HTTP Error 404`: a benign start-up race, before `lake-ddl` made the tables | n/a |
| +11 m | `lake-ddl` one-shot: `✓ 64 statements applied to catalog lake` | — |
| +13 m 01 s | First scheduled ingest run starts (the retired scheduler's, for the v3 lake since retired too) | n/a |
| +13 m 16 s | `docker compose ps` reaches its steady state (below) | — |
| +13 m 25 s | `rpk topic consume` returns an Avro record; 9 v3 topics + `_schemas`, 12 partitions each | — |
| +13 m 30 s | Prometheus 8/8 targets up, Grafana `/api/health` 200, ClickHouse `gold` has 17 tables | — |
| +14 m 21 s | First scheduled ingest run completes; raw/bronze/silver/gold all written | n/a |
| +15 m | `bash scripts/lake-verify.sh` → `all checks passed` | 1 m 42 s |

**Total: ≈15 min from zero to data in every layer** (≈25 min counting the failed first
attempt described in BUG 1). The offsets after the build are +3 m 01 s, +3 m 16 s and
+4 m 21 s measured from the container-start command itself, which is what a second run on
warm images will see.

The v3 lake's `lake-verify.sh` (archived) at +15 min reported raw rows on 79 gapless partitions, silver equal
to bronze, and gold equal to silver first deliveries for all three venues. ClickHouse then
held rows in both `gold.trades` and `gold.book_top20`.

## What healthy looks like

`make health` prints `health: PASS` (`scripts/health.sh`: container health, each venue's
capture and what Redpanda acknowledged from it in the last 2 minutes, a SKIP for the lake until
phase 004, the order-book switch, the backup timer; the
[operator guide](../operations/README.md#3-check-it-is-healthy) has every line).

Underneath it,
`docker compose ps -a` shows **21 containers: 17 long-running and 4 one-shots** (plain
`docker compose ps` hides the exited one-shots). Only 16 of
the 17 ever report `healthy`; `spark-iceberg` has no healthcheck defined and stays at plain
`Up`, which is correct and not a degraded state. Read from `docker ps -a` on the live stack on
2026-11-26, not from a fresh install, less `clickhouse`, which the ClickHouse pause removes
(ADR-057), and less `lake-metrics` and `lake-ddl`, which the retirement of the v3 lake removes;
it agrees with the healthchecks and `restart: "no"` in a copy of the compose file without those
two.

| State | Count | Containers |
|---|---|---|
| `Up (healthy)` | 16 | `redpanda`, `redpanda-console`, `minio`, `prometheus`, `grafana`, `cadvisor`, `prefect-db`, `lakekeeper`, `trino`, `capture-binance`, `capture-kraken`, `capture-coinbase`, `airflow-api-server`, `airflow-scheduler`, `airflow-dag-processor`, `airflow-statsd` |
| `Up`, no healthcheck | 1 | `spark-iceberg` |
| `Exited (0)` | 4 | `lakekeeper-migrate`, `redpanda-init`, `lake-init`, `airflow-migrate` |

An `Exited (0)` one-shot is success. A one-shot at a non-zero exit code is not — read its
logs before anything else, because the services after it in the dependency chain will come
up empty.

## Known benign noise

- **jemalloc CPU message from `clickhouse-client`**, while the `hot-gold` profile runs.
  `<jemalloc>: Number of CPUs detected is not deterministic` on every client invocation, a
  side effect of the `cpuset` pin. Harmless.

## BUG 1 — capture image build race

**Symptom**, `docker compose up -d` runs for ~10 minutes, builds every image, then aborts
with **zero containers started**:

```
target capture-coinbase: failed to solve: image "docker.io/library/k2-capture:v3": already exists
```

**Detection**, manual only, no alert covers a failed install. Recognise it by the
combination: the error names `already exists` for `k2-capture:v3`, and `docker compose ps`
afterwards lists nothing. `docker images` shows the image was in fact built.

**Cause**, `capture-binance`, `capture-kraken` and `capture-coinbase` each carried a
`build:` block resolving to the same `image: k2-capture:v3`. BuildKit builds all three in
parallel and the exports race on the tag; whichever two lose abort the whole `up`.

**Fix**, already applied in this tree: only `capture-binance` carries `build:`; the other
two reference the tag. See `docker-compose.yml` around the `x-capture-build` anchor.
Adding a fourth exchange must **not** add a second `build:` block
([adding-new-exchanges.md](../operations/adding-new-exchanges.md)).

**Workaround on an older checkout**, build the image once, then bring the stack up:

```bash
docker compose build capture-binance
docker compose up -d
```

**Measured**, the re-run after the fix created all 19 containers in **14.39 s** from cached
images. The lost time is the ~10 min of the first attempt, not the recovery.

---

## Failure modes / incidents

- **2026-08-28**, first fresh install after a Docker Desktop disk wipe (see
  [docker-desktop-disk.md](./docker-desktop-disk.md)). BUG 1 aborted the first attempt at
  +10 m 41 s. Fixed in `docker-compose.yml` and `docs/operations/adding-new-exchanges.md`;
  the re-run succeeded with no other intervention.

**Revisit when** a service is added to or removed from `docker-compose.yml` (the counts in
"what healthy looks like" become wrong), or when an image joins or leaves the build set (the
~9.5 min build figure moves).

**Last verified:** 2026-08-28 against a fresh clone on a 28-core host, Docker Desktop VM
40 GB / 28 CPU.
