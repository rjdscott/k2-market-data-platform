# Runbook: Bring the stack up from a fresh clone

Zero images, zero volumes, to a lake with data in every layer. Use this for a first
install, after a Docker wipe, or as the release gate's manual twin. It does not cover
upgrading a stack that already holds data, and it does not cover restoring the lake
(see [lake-recovery.md](./lake-recovery.md)).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Capture image build race aborts `docker compose up` | < 5 min | ~1 min re-run, 2026-08-28 (fixed in tree) |

---

## Prerequisites

- A Docker engine with **at least 24 GB of memory** so every declared limit is honoured.
  Measured usage on a fresh install is far lower (~4.5 GiB RSS total; largest single
  container ClickHouse at 1.13 GiB).
- **At least 15 CPU cores**, or an explicit cpuset. `docker-compose.yml` pins the three
  capture containers to `${K2_CAPTURE_CPUSET-12-14}` and the heavy tier (ClickHouse,
  Spark, lake-ddl) to `${K2_HEAVY_CPUSET-0-11}`. A cpuset naming a core the host does not
  have fails the container at start with `invalid argument`. On a smaller host, set both
  in `.env` to ranges that exist, or to the empty string to run unpinned — the compose
  default uses `-` and not `:-` so that an explicit empty value wins:

  ```bash
  K2_CAPTURE_CPUSET=
  K2_HEAVY_CPUSET=
  ```

  See [`.env.example`](../../.env.example) for the full note.
- **Roughly 8 GB of image space.** The three built images are `k2-capture` 54 MB,
  `k2-prefect-worker` 1.23 GB and `k2-spark-iceberg` 6.47 GB, on top of ~5.3 GB of
  upstream pulls.

## Command sequence

```bash
git clone https://github.com/rjdscott/k2-market-data-platform.git
cd k2-market-data-platform
cp .env.example .env            # set every change-me value;
                                # LAKEKEEPER_ENCRYPTION_KEY: openssl rand -base64 32
set -a && . ./.env && set +a
docker compose up -d            # first run pulls 9 images and builds 3
docker compose ps
docker exec k2-redpanda rpk topic consume market.crypto.v3.trades.binance --num 1
bash scripts/lake-verify.sh
```

## Timeline

Measured 2026-08-28 on a 28-core host, 40 GB Docker VM, 648 GB free, fast network.
Elapsed is from the start of the first `docker compose up -d`.

| Elapsed | What happens | Duration |
|---|---|---|
| 0 s | `docker compose up -d`, no output while it pulls and builds | — |
| +83 s | 9 upstream images pulled, ~5.3 GB | 83 s |
| +83 s → +10 m | Builds: `k2-capture`, `k2-prefect-worker`, `k2-spark-iceberg` | ~9.5 min, dominated by the 6.47 GB Spark image |
| +10 m | All 19 containers created from cached images | 14 s wall |
| +10 m 14 s | `lake-metrics` logs 15× `HTTP Error 404` — benign, see below | — |
| +11 m | `lake-ddl` one-shot: `✓ 64 statements applied to catalog lake` | — |
| +13 m 01 s | First Prefect `lake-ingest` flow run starts (deployment `lake-ingest-5min`) | — |
| +13 m 16 s | `docker compose ps` reaches its steady state (below) | — |
| +13 m 25 s | `rpk topic consume` returns an Avro record; 9 v3 topics + `_schemas`, 12 partitions each | — |
| +13 m 30 s | Prometheus 8/8 targets up, Grafana `/api/health` 200, ClickHouse `gold` has 17 tables | — |
| +14 m 21 s | First `lake-ingest` flow `Finished in state Completed()`; raw/bronze/silver/gold all written | — |
| +15 m | `bash scripts/lake-verify.sh` → `all checks passed` | 1 m 42 s |

**Total: ≈15 min from zero to data in every layer** (≈25 min counting the failed first
attempt described in BUG 1). The offsets after the build are +3 m 01 s, +3 m 16 s and
+4 m 21 s measured from the container-start command itself, which is what a second run on
warm images will see.

`lake-verify.sh` at +15 min reported 264,855 raw rows, 79 gapless partitions, silver equal
to bronze, and gold equal to silver first deliveries for all three venues. ClickHouse then
held 85,134 rows in `gold.trades` and 13,896 in `gold.book_top20`.

## What healthy looks like

`docker compose ps` shows **19 containers: 15 long-running and 4 one-shots.** Only 12 of
the 15 ever report `healthy`; three have no healthcheck defined and stay at plain `Up`,
which is correct and not a degraded state.

| State | Count | Containers |
|---|---|---|
| `Up (healthy)` | 12 | `redpanda`, `redpanda-console`, `clickhouse`, `minio`, `prometheus`, `grafana`, `prefect-db`, `prefect-server`, `lakekeeper`, `capture-binance`, `capture-kraken`, `capture-coinbase` |
| `Up`, no healthcheck | 3 | `lake-metrics`, `prefect-worker`, `spark-iceberg` |
| `Exited (0)` | 4 | `lakekeeper-migrate`, `redpanda-init`, `lake-init`, `lake-ddl` |

An `Exited (0)` one-shot is success. A one-shot at a non-zero exit code is not — read its
logs before anything else, because the services after it in the dependency chain will come
up empty.

## Known benign noise

- **`lake-metrics` 404s at boot.** Fifteen lines of
  `WARNING cannot read <table>: HTTP Error 404` in the first seconds, because the exporter
  starts scraping before `lake-ddl` has created the tables. None appear after `lake-ddl`
  completes. Cosmetic startup race, no action.
- **jemalloc CPU message from `clickhouse-client`.**
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
"what healthy looks like" become wrong), or when a fourth image joins the build set (the
~9.5 min build figure moves).

**Last verified:** 2026-08-28 against a fresh clone on a 28-core host, Docker Desktop VM
40 GB / 28 CPU.
