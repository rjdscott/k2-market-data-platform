# Operations: running the platform

How to run K2 day to day: start it, check it, reach its consoles, stop it, restart one part of
it, and roll out a change, in that order. Every command here runs from the root of the **live
checkout**, the directory the stack was started from (`docker compose ls` prints it), with the
secrets loaded into your shell:

```bash
set -a && . ./.env && set +a
```

[quick-reference.md](./quick-reference.md) is the companion: the per-component commands
(Redpanda, ClickHouse, capture, lake, Trino) on one page. Incidents have their own procedures
in [`../runbooks/`](../runbooks/README.md).

**Terms used on this page.** The *stack* is everything `docker-compose.yml` defines. A
*service* is one entry in that file (`clickhouse`); its *container* is `k2-<service>`
(`k2-clickhouse`). A *one-shot* is a service that sets something up and exits
(`restart: "no"`: `redpanda-init`, `lakekeeper-migrate`, `lake-init`, `lake-ddl`,
`airflow-migrate`); `Exited (0)` is its success state. The *hot tier* is ClickHouse's `gold`
database, fed live from Redpanda; the *lake* is the Iceberg tables on MinIO, the system of
record, written every 5 minutes by a Spark job. *Airflow* schedules that job and the others as
*DAGs* (`lake_ingest`, `lake_maintenance`, `lake_rebuild`, `dbt_marts`); a *paused* DAG starts
no new run. A *bind mount* is a file or directory of this checkout mounted into a container, so
the container reads the checkout's copy. *Loopback* is `127.0.0.1`, the address only this host
can reach.

---

## Read this first: the state of this host

Two conditions on the reference host, as of 2026-11-26, change the commands below.

**`make up` and `make dev-up` refuse to start this stack.** Both run `scripts/check-env.py`
first, which refuses a default or placeholder credential, and the live MinIO root credential is
MinIO's own default pair ([SECURITY.md](../../SECURITY.md)). Run 2026-11-26 in the live checkout:

```console
$ python3 scripts/check-env.py
check-env: FAIL .env: MINIO_ROOT_USER equals MINIO_ROOT_PASSWORD; set the password to `openssl rand -hex 24`
```

Do **not** edit the pair in `.env` to get past it: each Lakekeeper warehouse keeps its own
stored copy of the credential, and the lake stops writing. Until the replacement lands, start or
recreate services by name, which is what `make up` would have run:

```bash
docker compose up -d --no-deps <service> [<service> ...]                   # start, or apply a changed definition
docker compose up -d --force-recreate --no-deps <service> [<service> ...]  # also re-read a bind mount or a config read at start
```

`--no-deps` stops compose from also starting or recreating the services this one depends on.
**This ends** when the credential replacement designed in
`docs/research/2026-11-26-storage-credentials-least-privilege.md` (not yet merged) is merged and
rolled out: `python3 scripts/check-env.py` then prints `check-env: PASS`, and `make up` works
again. A fresh clone is not affected: it generates its own credentials (section 1).

**The host is short of memory.** The Docker VM is set to 50.5 GiB on a host with 62.6 GiB of
usable RAM, and it grows to that setting within hours, so the desktop and everything else must
fit in about 12 GiB. On 2026-11-25 they did not and the host killed the VM, every container with
it. Keep the desktop light, watch `free -h` for swap filling, and do not start a second stack or
an extra heavy container on this host. The arithmetic for any host is in
[docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md#2-size-the-vm-so-the-host-keeps-enough-memory).

---

## 1. Start from a fresh clone

You need Docker with Compose v2, `git`, `python3`, and enough memory: the Docker engine must
offer at least 38 GiB, so that every container's declared memory limit fits (37.812 GiB at the
bootstrap peak, when the one-shots run beside everything else; [setup.md](../development/setup.md)),
and on Docker Desktop the host must have more RAM than the VM's memory setting plus everything
else it runs ([sizing](../runbooks/docker-vm-oom-kill.md#2-size-the-vm-so-the-host-keeps-enough-memory)).
`docker info --format '{{.MemTotal}} {{.NCPU}}'` prints what the engine offers, in bytes and
CPUs; the reference host prints `53149335552 28` (49.5 GiB).

```bash
git clone https://github.com/rjdscott/k2-market-data-platform.git && cd k2-market-data-platform
cp .env.example .env

# Replace every change-me-in-production value with a random one: 24 random bytes as hex,
# and 32 random bytes as base64 for LAKEKEEPER_ENCRYPTION_KEY, which Lakekeeper requires.
python3 - <<'EOF'
import base64, pathlib, re, secrets
p = pathlib.Path(".env")
def fill(m):
    name = m.group(1)
    value = base64.b64encode(secrets.token_bytes(32)).decode() if name == "LAKEKEEPER_ENCRYPTION_KEY" else secrets.token_hex(24)
    return f"{name}={value}"
p.write_text(re.sub(r"^([A-Z0-9_]+)=change-me-in-production$", fill, p.read_text(), flags=re.M))
EOF
python3 scripts/check-env.py

set -a && . ./.env && set +a
make up         # check-env again, then docker compose up -d; builds four images on the first run
make health
```

Run 2026-11-26 against a copy of `.env.example`, the fill step left no placeholder and
check-env printed `check-env: PASS ... (MinIO user and password differ; no placeholder secrets)`.
`make up` and `make health` on a fresh clone were last timed on 2026-08-28, about 15 minutes
from nothing to data in every lake layer, most of it building the Spark image
([fresh-install.md](../runbooks/fresh-install.md), which also covers the build race and the
benign start-up noise). Three `.env` values are host-specific, not secrets:

- `K2_CAPTURE_CPUSET` and `K2_HEAVY_CPUSET` ship empty: no CPU pinning, starts on any host.
  Set the 15-core layout from `.env.example` only on a host that has those cores, or six
  containers fail at start with `invalid argument`.
- `K2_AIRFLOW_PORT` (8090) and `K2_TRINO_PORT` (8088) move those two consoles if something
  else on the host owns the port. `K2_DOCKER_GID` is the group that owns the Docker socket, `0`
  on Docker Desktop.

---

## 2. Start a machine that already holds data

All data lives in named Docker volumes (Redpanda, ClickHouse, MinIO for the lake, PostgreSQL
for the Airflow and Lakekeeper metadata, Prometheus, Grafana), which survive stopping, `make
down` and a host reboot. `docker compose up -d` may run the one-shots again; each is written to
be re-run (every step is create-if-missing or converge, per the header of `docker/redpanda/init.sh`,
`docker/lake/init-lake.sh` and `docker/lake/apply_ddl.py`), so a re-run changes nothing.

**After a host reboot or a Docker Desktop restart**, every long-running service has `restart:
unless-stopped` and comes back by itself, unless you stopped it before. Start Docker Desktop if
it is not running (`systemctl --user start docker-desktop`), then list any long-running service
that did not come back. Run 2026-11-26 on the healthy stack, it printed nothing:

```bash
comm -23 <(docker compose config --services | grep -v -x -E 'airflow-migrate|lakekeeper-migrate|lake-init|lake-ddl|redpanda-init' | sort) \
         <(docker compose ps --services --status running | sort)
```

Start each service it names with `docker compose up -d --no-deps <service>`. On 2026-11-25,
after the host killed the VM, it would have named `prometheus`, which stayed `Exited (255)`
despite its restart policy ([docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md)).

**After `make down` or `docker compose stop`**, the containers are gone or stopped:

```bash
make up                  # elsewhere; on this host it refuses (Read this first), so instead:
docker compose up -d     # the same compose command make up runs, without the credential check
```

That bare `docker compose up -d` is the one sanctioned use of it on this host, and only to bring
back a stack that already ran with this `.env`: it skips `check-env.py`, which is the guard a
new stack needs. <!-- VERIFY: not run on this stack since the loopback change; expect every container healthy within about 5 minutes -->

**Then, either way:** unpause any DAG you paused before stopping (section 6, step 5; a pause is
kept in Airflow's database across restarts), run `make health` (section 3), and if capture was
down, record the window as the runbook's step 8 does.

---

## 3. Check it is healthy

```bash
make health
```

It takes about 5 seconds on a healthy stack and up to 5 minutes on a starting one (it waits for
containers to turn healthy). It exits 1 if any line says FAIL; a WARN is "warming up or worth a
look" and does not fail it. Run 2026-11-26, with the per-venue trade counts replaced because
they are market data:

```console
$ make health
== (1) container health (<= 5 min) ==
(1) health: PASS (all services healthy or have no healthcheck)
== (2) venues, last 2 minutes ==
(2) coinbase: PASS (<n> trades/2min)
(2) kraken: PASS (<n> trades/2min)
(2) binance: PASS (<n> trades/2min)
(2) capture-binance metrics: PASS (k2_capture_last_message_ts_seconds{exchange="binance",stream="trade"} 1795656104.15...
(2) capture-kraken metrics: PASS (...)
(2) capture-coinbase metrics: PASS (...)
== (3) lake ==
(3) lake commit age: PASS (last commit 173s ago)
(3) last lake_ingest run: PASS (scheduled__2026-11-26T01:16:00+00:00 success)
== summary ==
health: PASS
```

Line by line (`scripts/health.sh`):

| Line | What it checks | A FAIL or WARN means | Open |
|---|---|---|---|
| `(1) health` | Every running container of this compose project reports `healthy`, or has no healthcheck (`spark-iceberg`, `lake-metrics`). Waits up to 5 min (`K2_HEALTH_WAIT`) | `FAIL (no containers in project '<name>')`: you are not in the live checkout (a git worktree is another compose project), or the stack is down. `FAIL (still unhealthy after 5 min)` lists the containers: read that one's `docker logs --tail 100 k2-<service>` | capture: [capture-down.md](../runbooks/capture-down.md); `redpanda`: [redpanda.md](../runbooks/redpanda.md); `clickhouse`: [failure-recovery.md](../runbooks/failure-recovery.md) section 2; `minio`, `lakekeeper`: [lake-recovery.md](../runbooks/lake-recovery.md); `airflow-*`: [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 1; `trino`: [dbt-marts.md](../runbooks/dbt-marts.md) |
| `(1)`, its blind spot | A container that has **exited** is not in the list `(1)` reads, so it passes with, for example, Prometheus down | run the `comm` check in section 2 | section 2 |
| `(2) <venue>` | Trades of that venue in ClickHouse `gold.trades` with an exchange timestamp in the last 2 minutes. Never FAIL | `WARN (0 trades/2min)`, or **a venue missing from the list** (only venues with rows print): nothing reached the hot tier from it. `WARN (no rows ...)` for all: ClickHouse unreachable, or nothing is flowing | [capture-feed-stale.md](../runbooks/capture-feed-stale.md); everything silent: [failure-recovery.md](../runbooks/failure-recovery.md) sections 1 and 2 |
| `(2) capture-<venue> metrics` | Reads each capture's `/metrics` (port 8082 inside the compose network, through a throwaway `curlimages/curl` container) and prints the last-message time per stream, in Unix seconds. It does not judge staleness | `WARN (no k2_capture_last_message_ts_seconds yet ...)`: the capture is not serving metrics | [capture-down.md](../runbooks/capture-down.md); staleness per stream: `docker exec k2-capture-<venue> /k2-capture healthcheck` |
| `(3) lake commit age` | Seconds since the newest commit to any lake table, from Prometheus (`time() - max(k2_lake_last_commit_ts_seconds)`), at most 1800 (`K2_HEALTH_LAKE_MAX_AGE`) | `FAIL (Prometheus has no k2_lake_last_commit_ts_seconds ...)`: Prometheus is down (`curl -s localhost:9090/-/healthy`), the `lake-metrics` exporter is down, or the lake never committed. `FAIL (last commit <n>s ago ...)`: the lake is stale | Prometheus down: section 6; exporter: [lake-recovery.md](../runbooks/lake-recovery.md); stale: [lake-ingest-lag.md](../runbooks/lake-ingest-lag.md) section 2 |
| `(3) last lake_ingest run` | The newest finished scheduled `lake_ingest` run of the last 30 minutes, from the Airflow CLI in `k2-airflow-scheduler`. `PASS` on `success`, and on skips while `lake_maintenance` or `lake_rebuild` holds the lake's writer lock (a run that finds the lock taken exits 75 and is marked skipped) | `FAIL (the Airflow CLI ... did not answer)`: the scheduler is down. `FAIL (no lake_ingest run finished ...)`: the DAG is paused or stale, or the scheduler stopped (a deliberate pause shows this too). `FAIL (<run> failed ...)`. `WARN (1 or 2 skipped ...)`, then `FAIL` at 3 skipped with nothing holding the lock | [airflow-scheduling.md](../runbooks/airflow-scheduling.md): section 1, sections 5 and 1, section 2, section 3 |

Beyond `make health`, three reads worth a glance once a day:

```bash
# Nothing firing (prints nothing when no alert is pending or firing; run 2026-11-26: nothing)
curl -s localhost:9090/api/v1/alerts | jq -r '.data.alerts[] | "\(.labels.alertname)\t\(.state)"'

# Lake ingest lag, seconds behind the newest Kafka record; LakeIngestLagHigh fires at 900
curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=time() - k2_lake_max_kafka_ts_seconds' \
  | jq -r '.data.result[].value[1]'

# Capture produce errors per venue since each capture started; 0 is the only good value
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=sum by (exchange) (k2_capture_produce_errors_total)' \
  | jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'
```

Run 2026-11-26: the lag read `286.89...`, inside one 5-minute cycle plus a run, and the produce
errors `binance 0`, `coinbase 0`, `kraken 0`. All 36 alert rules, what each means and its
runbook: [observability.md](./observability.md#alert-rules).

---

## 4. Consoles and endpoints

Every port is published on `127.0.0.1` only (`docker-compose.yml`, checked by
`scripts/check-budget.py` in CI), so these URLs work on this host and nowhere else; other users
of this host can still reach them ([SECURITY.md](../../SECURITY.md)). Logins are named by the
`.env` variable that holds them; read the value from your `.env`, never from a document. Each
URL answered on 2026-11-26 (`curl -s -o /dev/null -w '%{http_code}'`: 200 unless noted).

| Service | URL | What it is for | Login |
|---|---|---|---|
| Grafana | http://localhost:3000 | The dashboards: start at `k2-pipeline-overview`, then `k2-lake` and `k2-resources` ([dashboards.md](./dashboards.md)) | user `admin`, password `GRAFANA_PASSWORD` |
| Airflow | http://localhost:8090 (`K2_AIRFLOW_PORT`) | The four DAGs, their runs, task logs, pause and unpause | user `admin`; password generated by Airflow on first start: `docker exec k2-airflow-api-server cat /opt/airflow/simple_auth_manager_passwords.json.generated` |
| Redpanda Console | http://localhost:8080 | Topics, consumer groups, live messages, decoded as Avro through the schema registry | none |
| Trino | http://localhost:8088/ui/ (`K2_TRINO_PORT`) | SQL over every lake table (catalog `lake`, read-only) and the dbt marts; the UI shows running and recent queries | any user name; not authenticated |
| Lakekeeper | http://localhost:18181/ui/ | Browse the Iceberg catalog: warehouse `k2` holds the lake (`raw`, `bronze`, `silver`, `gold`, `audit`); one warehouse per data domain holds its marts | none (authorization is off) |
| MinIO console | http://localhost:9001 | The objects under the lake, bucket `k2-lake` | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| Prometheus | http://localhost:9090 | Metrics, scrape targets (`/targets`) and alert state (`/alerts`) | none |
| ClickHouse | http://localhost:8123/play | A query box over the hot tier, `SELECT count() FROM gold.trades FINAL` | user `default`, `CLICKHOUSE_PASSWORD`; read-only research user `quant`, `K2_QUANT_PASSWORD` |
| Spark master | http://localhost:18080 | Mostly empty by design: the lake jobs run as local drivers and never register with it | none |
| Spark Thrift server | http://localhost:4040 | The UI of the image's always-on Thrift JDBC server (answers 302), **not** of the lake jobs. A running lake job's UI is port 4041 inside the container and is not published: `docker exec k2-spark-iceberg curl -s localhost:4041/api/v1/applications` | none |
| JupyterLab | http://localhost:8889 | The research notebooks; only while `make notebooks` runs in a terminal (answers 000 otherwise) | token printed by `make notebooks` |
| Prefect | `http://localhost:$K2_PREFECT_PORT` (4200 by default) | Not running: the Prefect rollback sits behind the compose `legacy` profile ([airflow-scheduling.md](../runbooks/airflow-scheduling.md#rollback-to-prefect)) | none |

APIs rather than consoles, also on `127.0.0.1`: Redpanda's Kafka API `19092` (the listener host
tools use; `9092` is advertised as `redpanda:9092`, for containers), its Admin API `9644` and
Schema Registry `8081`; ClickHouse's native protocol `9002` and its Prometheus endpoint `9363`;
MinIO's S3 API `9000`; PostgreSQL `15432` (`prefect-db`: the Airflow, Lakekeeper and Prefect
metadata). The capture containers' `/metrics` (8082) is not published: Prometheus reads it on
the compose network.

### From another machine: an SSH tunnel, never an open port

Do not publish a port on `0.0.0.0` or the host's LAN address to reach a console: most of them
have no login, and MinIO's root credential is the known default (SECURITY.md). Forward the
loopback port over SSH instead, from the other machine:

```bash
ssh -N -L 127.0.0.1:3000:127.0.0.1:3000 -L 127.0.0.1:8090:127.0.0.1:8090 <user>@<k2-host>
# then open http://localhost:3000 and http://localhost:8090 on the other machine
```

`127.0.0.1:` on the left keeps the forwarded port on that machine's loopback too. Keep the
same port number on both sides for Airflow: its UI builds links from `http://localhost:8090`
(`AIRFLOW__API__BASE_URL`). This needs an SSH server on the K2 host; the reference host runs
none (`systemctl is-active ssh` printed `inactive` on 2026-11-26), so the tunnel is not yet run
here. <!-- VERIFY: run from a second machine once an SSH server is installed -->

---

## 5. Stop without losing data

The one loss no procedure prevents: **while capture is stopped, the venues' market data is not
recorded, and the venues' public feeds do not replay it.** Everything already captured is kept.

```bash
# 1. The lake has caught up: under 900 s (section 3's lag read).
curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=time() - k2_lake_max_kafka_ts_seconds' \
  | jq -r '.data.result[].value[1]'

# 2. No lake or dbt job in flight: pause and wait, exactly as section 6 steps 0 to 2.

# 3. Stop every container and keep it, with its logs. -t 60 gives each one 60 s to shut
#    down cleanly before Docker kills it (the default is 10 s).
docker compose stop -t 60
```

<!-- VERIFY: steps 2 and 3 not run as a whole-stack stop on this stack; the pause and wait were run for the 2026-11-25 rollout -->

Start again with `docker compose start` (or section 2), then unpause what you paused.

`make down` (`docker compose down`) also stops everything and keeps the volumes, but it
**removes the containers**, and with them three things that live only in a container: every
container's `docker logs`, the Airflow task logs (`/opt/airflow/logs` in `k2-airflow-scheduler`)
and the Spark event logs (`/home/iceberg/spark-events` in `k2-spark-iceberg`). Use it when the
containers or the network must be rebuilt, not to pause the platform.

**Never `docker compose down -v`** on a stack whose data you want: `-v` deletes the volumes,
which are the lake, the hot tier, Redpanda, and the catalog's metadata.

**A hard stop** (the VM or host killed, power lost) loses more than a clean one. Measured
2026-11-25, when the host killed the Docker VM: capture was off 343 s; for the minute of the kill
the hot tier and the lake hold different partial sets of trades, because Redpanda runs without
fsync (`--unsafe-bypass-fsync=true`) and acknowledged records still in the VM's memory died
with it; the lake run in flight committed nothing and Airflow retried it; Prometheus did not
restart by itself. The procedure is [docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md).
A stop longer than about 48 hours has one more risk: Redpanda keeps raw records for 48 h or 2 GiB
per partition, so records the lake had not read before the stop can be deleted at restart before
the ingest reaches them. Step 1 is what rules that out.

---

## 6. Restart or recreate one service

`docker compose restart <service>` restarts the process in the same container. `docker compose
up -d --no-deps <service>` creates a new container if the service's definition changed, and
`--force-recreate` creates one regardless, which is what re-reads a bind-mounted file (see the
rule below). `-t <seconds>` gives the old container that long to stop cleanly.

### Which services need the pause

| Service | When it is safe | What a restart costs | As run 2026-11-25, 23:50 to 23:54 UTC |
|---|---|---|---|
| `prometheus`, `grafana`, `redpanda-console`, `cadvisor`, `lake-metrics` | any time | a gap of seconds in scrapes or dashboards; alerts re-evaluate after it | `prometheus`, `grafana`, `redpanda-console`: seconds each |
| `clickhouse` | any time, with `-t 60`: its Kafka consumers resume from their committed offsets, and duplicates collapse under `FINAL` | hot-tier queries fail while it restarts | `-t 60`, healthy in 8 s |
| `capture-binance`, `capture-kraken`, `capture-coinbase` | any time for correctness, one venue at a time | **that venue's market data while it is down, for good**; a book resync on reconnect | not restarted |
| `redpanda` | with the lake DAGs paused: the ingest reads it | capture holds its records in its producer queue for the restart | `-t 30`, healthy in 13 s; `k2_capture_produce_errors_total` 0 on all three venues before and after |
| `minio`, `lakekeeper`, `spark-iceberg`, `trino`, `prefect-db` | with `lake_ingest`, `lake_maintenance` and `dbt_marts` paused and nothing running: every lake and mart write goes through them, and `prefect-db` holds Airflow's and Lakekeeper's databases | a run cut mid-way commits nothing and fails or retries; pausing keeps the record clean | `minio`: healthy in 6 s; `trino` with `--force-recreate`: 10 s; `spark-iceberg`: seconds. `lakekeeper` and `prefect-db` not yet recreated on this stack |
| `airflow-scheduler`, `airflow-api-server`, `airflow-dag-processor` | paused and nothing running: tasks run inside the scheduler and report to the api-server | a running task dies with them | not in this rollout; see [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 3 |
| `airflow-statsd` | as the row above: after recreating it, restart the three Airflow services, which resolve its address only at start | every Airflow metric stops until they restart | [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 3 |

### The pattern: pause, wait for idle, recreate, verify, unpause

```bash
# 0. Note which DAGs are paused now, so that step 5 unpauses only what you paused.
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list -o json 2>/dev/null | tail -n 1 \
  | python3 -c 'import json,sys; [print(d["dag_id"], "paused" if d["is_paused"] else "unpaused") for d in json.load(sys.stdin)]'

# 1. Pause the scheduled DAGs (as run on 2026-11-25 before the rollout). lake_rebuild runs
#    only by hand and needs no pause; step 2 still waits for it.
docker exec k2-airflow-scheduler airflow dags pause lake_ingest
docker exec k2-airflow-scheduler airflow dags pause lake_maintenance
docker exec k2-airflow-scheduler airflow dags pause dbt_marts
```

Step 2 waits until nothing is running. It **fails closed**: anything it cannot read, a
container that does not answer or output it cannot parse, counts as busy, so a broken check
never reads as "idle". It checks for a lake job's process in the Spark container and for a
queued or running task in any running run of the four DAGs (a paused DAG's run can stay
`running` after its task has finished, so the task is what counts). Paste the two functions
once per shell; they work in bash and zsh.

```bash
# 2. Wait for idle: polls every 10 s, gives up after 15 min with exit 1.
lake_idle() {   # 0 only when every check answered and nothing runs
  local n dag runs run
  n=$(docker exec k2-spark-iceberg pgrep -cf 'lake/(ingest|maintenance|rebuild)\.py' 2>/dev/null)
  [ "$n" = 0 ] || return 1
  for dag in lake_ingest lake_maintenance lake_rebuild dbt_marts; do
    runs=$(docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list-runs "$dag" --state running -o json 2>/dev/null \
      | tail -n 1 | python3 -c 'import json,sys; print("\n".join(r["run_id"] for r in json.load(sys.stdin)))' 2>/dev/null) || return 1
    while read -r run; do
      [ -n "$run" ] || continue
      docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow tasks states-for-dag-run "$dag" "$run" -o json 2>/dev/null \
        | tail -n 1 | python3 -c 'import json,sys; sys.exit(any(t["state"] in ("queued", "running") for t in json.load(sys.stdin)))' 2>/dev/null || return 1
    done <<< "$runs"
  done
}
wait_idle() {
  local end=$((SECONDS + 900))
  until lake_idle; do
    [ "$SECONDS" -lt "$end" ] || { echo "wait_idle: busy or unreadable after 15 min: recreate nothing" >&2; return 1; }
    sleep 10
  done
  echo "wait_idle: idle at $(date -u +%T) UTC"
}
wait_idle
```

Run 2026-11-26 with the DAGs unpaused (read-only): `lake_idle` returned 1 while an ingest ran
and 0 in the gap after it, in bash and in zsh; with the Spark or the scheduler container name
misspelt it returned 1; `wait_idle` printed `wait_idle: idle at 01:29:13 UTC` after 5.6 s. Each
poll takes about 5 s. Pick a time well clear of 03:00 UTC, when `lake_maintenance` holds the lake
(23.6 minutes on 2026-11-25, up to its 2-hour timeout), and do not start step 3 unless step 2
printed `idle`.

```bash
# 3. Recreate (or start) the service. Chain it to the wait so a timeout stops it:
wait_idle && docker compose up -d --no-deps <service>                      # definition changed
wait_idle && docker compose up -d --force-recreate --no-deps <service>     # re-read mounts or config
#    with -t 60 for clickhouse and -t 30 for redpanda, as run on 2026-11-25

# 4. Verify: the container is healthy (spark-iceberg and lake-metrics have no healthcheck,
#    so read .State.Status for them: running), then the whole stack.
docker inspect -f '{{.State.Health.Status}}' k2-<service>
make health

# 5. Unpause what step 1 paused, and only that (step 0's list).
docker exec k2-airflow-scheduler airflow dags unpause lake_ingest
docker exec k2-airflow-scheduler airflow dags unpause lake_maintenance
docker exec k2-airflow-scheduler airflow dags unpause dbt_marts
```

Steps 1, 3 and 5 are as run on 2026-11-25 for the loopback rollout, the timings in the table
above; a later per-minute comparison of lake candles with ClickHouse trades showed no
divergence in that window. With `catchup` off, an unpaused DAG runs its most recent slot at once,
then on its schedule.

### The bind-mount rule

A bind mount makes a container read this checkout's copy of a file or directory, and Docker
holds it by inode, the filesystem's identity for a file rather than its name. Git and most
editors save by writing a new file and renaming it over the old one, which gives it a new inode.

- **A file mount** (`config/instruments.yaml` and `config/bars.yaml` in `spark-iceberg`,
  `docker/clickhouse/config.xml` and `users.xml`, `docker/prometheus/prometheus.yml`,
  `docker/airflow/statsd-mapping.yml`) keeps the old inode: the container goes on reading the
  old file until it is **recreated**. `docker restart` and `docker compose restart` do not fix
  it.
- **A directory mount** (`docker/lake`, `docker/airflow/dags`, `dbt`, `docker/trino/etc`,
  `docker/prometheus/rules`, `docker/grafana/*`, `config/` in the captures) sees files changed
  inside it: on 2026-11-26 every file changed in `dbt/` since `k2-airflow-scheduler` was created
  had the same sha256 inside the container as on the host. It goes stale when the directory
  itself is deleted and recreated, which a `git checkout` of a commit without it does: on
  2026-11-25 both Airflow containers saw an empty DAG folder for 35 minutes. Recreate every
  container that mounts it after any such switch.
- **A service that reads its files only at start** (Trino, Prometheus's rules, Grafana's
  provisioning, every capture's instrument list) needs the step in section 7 even when its mount
  is current.

Check what a container sees before you debug it:

```bash
sha256sum docker/lake/ingest.py; docker exec k2-spark-iceberg sha256sum /home/iceberg/lake/ingest.py
```

---

## 7. Roll out a merged change

The live checkout is bind-mounted into the running containers, so **a `git pull` in the live
checkout is a deploy** for everything that reads its files at run time: the next lake job runs
the new `docker/lake/` code, the next `dbt_marts` run builds the new `dbt/` models, and the
dag-processor re-reads a changed DAG file within 30 s (`min_file_process_interval`). There is no
separate release step, so pull only what you mean to run.

```bash
git status --short                         # must print nothing: a pull onto local edits is not a deploy of main
git fetch origin
git log --oneline HEAD..origin/main        # what will deploy
git diff --name-only HEAD origin/main      # which paths: find each in the table below
git pull --ff-only
```

Then, for each path that changed:

| Changed | Read by | After the pull |
|---|---|---|
| `docker/lake/*.py` | each lake job, fresh per run | nothing: the next run uses it. `metrics.py` is also the long-running `lake-metrics`: `docker compose up -d --force-recreate --no-deps lake-metrics` |
| `docker/lake/ddl/` (lake table DDL) | the `lake-ddl` one-shot | pause, wait, then `docker compose up -d --force-recreate --no-deps lake-ddl` |
| `dbt/` models, macros, YAML | the next `dbt_marts` run | nothing |
| `dbt/uv.lock`, `dbt/package-lock.yml`, `docker/airflow/Dockerfile` | baked into the image `k2-airflow:3.3.2` | `docker compose build airflow-migrate` (the only service that builds it), then pause, wait, `docker compose up -d --force-recreate --no-deps airflow-api-server airflow-scheduler airflow-dag-processor` |
| `docker/airflow/dags/` | the dag-processor | nothing; confirm with `docker exec k2-airflow-scheduler airflow dags list-import-errors` (`No data found`) |
| `docker/airflow/statsd-mapping.yml` | file mount | section 6, the `airflow-statsd` row |
| `docker/prometheus/rules/` | read at start and on SIGHUP | `docker kill -s HUP k2-prometheus`, then check `/rules` lists the change |
| `docker/prometheus/prometheus.yml` | file mount | `docker compose up -d --force-recreate --no-deps prometheus` |
| `docker/grafana/dashboards/` | polled every 30 s | nothing |
| `docker/grafana/provisioning/` | read at start | `docker compose up -d --force-recreate --no-deps grafana` |
| `docker/trino/etc/` (including the catalogs `make generate` writes) | read at start | pause `dbt_marts`, wait, `docker compose up -d --force-recreate --no-deps trino` |
| `docker/clickhouse/config.xml`, `users.xml` | file mounts | `docker compose up -d --force-recreate --no-deps -t 60 clickhouse` |
| `docker/clickhouse/ddl/` | only an empty volume | apply by hand: [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md#applying-it-to-a-running-server) |
| `config/instruments.yaml` | the captures at start; file mount in `spark-iceberg` | recreate the three `capture-*` one at a time (each loses its venue's data while down), and `spark-iceberg` under the pause |
| `docker/redpanda/`, `schemas/avro/` | the `redpanda-init` one-shot | `docker compose up -d --force-recreate --no-deps redpanda-init`: every step is create-if-missing or converge |
| `services/capture-rust/` | the image `k2-capture:v3` | `make build-capture`, then recreate the three captures one at a time |
| `docker/spark/Dockerfile` | the image `k2-spark-iceberg` | `docker compose build spark-iceberg`, then pause, wait, recreate `spark-iceberg` |
| `docker/lake-metrics/Dockerfile` | the image `k2-lake-metrics` | `docker compose build lake-metrics`, then recreate `lake-metrics` |
| `docker-compose.yml` | compose | `docker compose up -d --no-deps <each changed service>`, under the pause where section 6 asks for one |

Finish with `make health`. A change that touches several rows takes each row's step, the
paused ones inside one pause.

---

## 8. Logs

| What | Where | Kept until |
|---|---|---|
| Any container's output | `docker compose logs --since 10m <service>`, or `docker logs -f k2-<service>`; `make logs` follows all of them | the container is removed (`make down`, a recreate). Save it first: `docker logs k2-<service> > <file> 2>&1` |
| Capture | `docker logs k2-capture-<venue>`; `RUST_LOG` sets the level | as above |
| A lake job's own output (ingest, maintenance, rebuild) | the Airflow task log: the UI, or `docker exec k2-airflow-scheduler sh -c "find /opt/airflow/logs -path '*dag_id=lake_ingest*' -name '*.log' \| sort \| tail -2"` and read the older one; the newest can still be running | the scheduler container is removed |
| dbt runs | the Airflow task logs of `dbt_marts`, the same way | as above |
| Spark job detail | event logs in `/home/iceberg/spark-events` in `k2-spark-iceberg` | `spark-iceberg` is removed |
| ClickHouse server | `docker exec k2-clickhouse tail -n 50 /var/log/clickhouse-server/clickhouse-server.err.log` | a volume (`clickhouse-logs`): survives recreates |
| Docker Desktop itself, and the host's kernel | `journalctl --user -u docker-desktop --utc`; `journalctl -k --utc` | the host's journal |

---

## 9. When something looks wrong

1. `make health`, and read its first FAIL line against the table in section 3.
2. What is firing: `curl -s localhost:9090/api/v1/alerts | jq -r '.data.alerts[] | "\(.labels.alertname)\t\(.state)"'`.
   Each alert's `runbook` annotation names the page to open ([runbook index](../runbooks/README.md)).
3. Is anything down: the `comm` check in section 2, and `docker compose ps -a`.
4. **Everything restarted at once, or Docker does not answer:** the host may have killed the
   VM: `systemctl --user status docker-desktop` and
   [docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md).
5. Before any repair that deletes or rewrites data, read the runbook to the end: a lake gap is
   usually something to record, not to fix, because the venues' feeds do not replay
   ([runbook triage](../runbooks/README.md#triage)).

---

## Guides

| Doc | What it covers |
|-----|----------------|
| [quick-reference.md](./quick-reference.md) | The per-component commands: Redpanda, ClickHouse, capture, the lake, Trino |
| [data-catalog.md](./data-catalog.md) | Every user-facing table: grain, partitioning, time columns and units, symbol conventions, dedup key, which engine is authoritative, one runnable example each |
| [data-inspection.md](./data-inspection.md) | Operator health queries per hop: Redpanda, ClickHouse `gold`, the Iceberg lake |
| [observability.md](./observability.md) | Grafana dashboards, capture-tier and lake metrics, all 36 Prometheus alert rules |
| [dashboards.md](./dashboards.md) | Every dashboard and console as a screenshot of a live run, in the order the data moves, with what to read from each and the runbook it leads to. Written by `make screenshots` |
| [latency-budgets.md](./latency-budgets.md) | 7-segment latency budget plus the measured 2026-02-19 p50/p99 numbers |
| [docker-resources.md](./docker-resources.md) | Per-service CPU/RAM limits, 19.30 CPU / 35.312 GiB across 19 long-running services, +5 one-shot (2.00 / 2.500 GiB), bootstrap peak 21.30 / 37.812 GiB against the 25 / 48 GiB ceiling; the Prefect rollback behind `--profile legacy` |
| [airflow-schedules.md](./airflow-schedules.md) | The four Airflow DAGs, their crons, limits and runbooks: `lake_ingest`, `lake_maintenance`, `lake_rebuild` and the hourly `dbt_marts` |
| [prefect-schedules.md](./prefect-schedules.md) | The two Prefect schedules Airflow replaced at the cutover, kept for the rollback: `lake-ingest-5min` and `lake-maintenance-daily` |
| [clickhouse-database-standard.md](./clickhouse-database-standard.md) | Why everything served lives in the `gold` database (`k2` was dropped at the Phase E cutover) and how to keep it that way |
| [adding-new-exchanges.md](./adding-new-exchanges.md) | End-to-end checklist for wiring up a 4th exchange |
| [cost-model.md](./cost-model.md) | What this single host would cost as managed cloud services |

Incident procedures are in [`../runbooks/`](../runbooks/README.md), indexed by the alert that
fires. The archived v2 procedures (the Kotlin feed handler's in
[`legacy/v2-kotlin/runbooks/`](../../legacy/v2-kotlin/runbooks/feed-handler-crash.md), the
offload's in [`legacy/v2-offload/`](../../legacy/v2-offload/README.md)) and v1's in
[`legacy/v1/docs/runbooks/`](../../legacy/v1/docs/runbooks/) describe a stack that no longer
exists.

## Related

- [Architecture](../architecture/README.md), how the pipeline is designed
- [Decisions](../adr/README.md), ADR-001 to ADR-049
- [Development](../development/setup.md), the inner loops for changing Rust or Python code, and [testing](../development/testing.md)
