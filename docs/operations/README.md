# Operations: running the platform

How to run K2 day to day: start it, check it, reach its consoles, stop it, restart one part of
it, roll out a change, and back up what cannot be rebuilt, in that order. Every command here runs from the root of the **live
checkout**, the directory the stack was started from (`docker compose ls` prints it), with the
secrets loaded into your shell:

```bash
set -a && . ./.env && set +a
```

[quick-reference.md](./quick-reference.md) is the companion: the per-component commands
(Redpanda, ClickHouse while it runs, capture, lake, Trino) on one page. Incidents have their own procedures
in [`../runbooks/`](../runbooks/README.md).

**Terms used on this page.** The *stack* is everything `docker-compose.yml` defines. A
*service* is one entry in that file (`clickhouse`); its *container* is `k2-<service>`
(`k2-clickhouse`). A *one-shot* is a service that sets something up and exits
(`restart: "no"`: `redpanda-init`, `lakekeeper-migrate`, `lake-init`, `lake-ddl`,
`airflow-migrate`); `Exited (0)` is its success state. *Redpanda* is the message broker the
capture processes write every venue message to (it speaks the Kafka protocol). The *hot tier* is
ClickHouse's `gold` database, fed live from Redpanda when it runs; its tables keep duplicate
deliveries until a background merge, and `FINAL` in a query collapses them. A *compose profile*
is a label that keeps a service out of `docker compose up` unless that profile is named. The *lake* is the system of record:
tables in the *Iceberg* format (data files plus metadata that make a set of files one table with
atomic commits), stored in *MinIO* (an S3-compatible object store) and listed by *Lakekeeper*,
the catalog that names each table; a *warehouse* is one named lake in that catalog, `k2` for the
lake and one per data domain for its marts ([glossary](../adr/glossary.md#warehouse-catalog-and-schema)).
A Spark job writes the lake every 5 minutes. *Trino* is the SQL engine over the lake; *dbt*
builds *marts*, tables derived from the lake for one use, through Trino
([glossary](../adr/glossary.md#mart)). *Airflow* schedules the Spark and dbt jobs as *DAGs*
(`lake_ingest`, `lake_maintenance`, `lake_rebuild`, `dbt_marts`); a *paused* DAG starts no new
run. A *bind mount* is a file or directory of this checkout mounted into a container, so the
container reads the checkout's copy. *Loopback* is `127.0.0.1`, the address only this host can
reach.

---

## Read this first: the state of this host

Two conditions on the *reference host*, the maintainer's machine where the live stack runs and
where every command on this page was checked, change the commands below as of 2026-11-26. On
another host, check the first with `python3 scripts/check-env.py` and the second with the
sizing arithmetic linked below.

**`make up` and `make dev-up` refuse to start this stack.** Both run `scripts/check-env.py`
first, which refuses a credential that is a placeholder, equal to its user, a known default or
under 16 characters, and the live MinIO root credential is MinIO's own default pair
([SECURITY.md](../../SECURITY.md)). It names each variable it refuses, never a value. What it
prints for the shipped `.env.example`, where every secret is still its placeholder (run
2026-11-26; never point it at the live `.env` to copy its output into a document):

```console
$ python3 scripts/check-env.py --env-file .env.example
check-env: FAIL .env.example: CLICKHOUSE_PASSWORD is unset, empty or still the .env.example placeholder
check-env: FAIL .env.example: K2_QUANT_PASSWORD is unset, empty or still the .env.example placeholder
check-env: FAIL .env.example: PREFECT_DB_PASSWORD is unset, empty or still the .env.example placeholder
check-env: FAIL .env.example: MINIO_ROOT_PASSWORD is unset, empty or still the .env.example placeholder
check-env: FAIL .env.example: LAKEKEEPER_ENCRYPTION_KEY is unset, empty or still the .env.example placeholder
check-env: FAIL .env.example: AIRFLOW_DB_PASSWORD is unset, empty or still the .env.example placeholder
check-env: FAIL .env.example: AIRFLOW_JWT_SECRET is unset, empty or still the .env.example placeholder
check-env: FAIL .env.example: AIRFLOW_API_SECRET_KEY is unset, empty or still the .env.example placeholder
check-env: FAIL .env.example: GRAFANA_PASSWORD is unset, empty or still the .env.example placeholder
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
again. Do not try to escape it with a fresh clone on this host: section 1 says why.

**The host is short of memory.** The Docker VM is set to 50.5 GiB on a host with 62.6 GiB of
usable RAM, and it grows to that setting within hours, so the desktop and everything else must
fit in about 12 GiB. On 2026-11-25 they did not and the host killed the VM, every container with
it. Keep the desktop light, watch `free -h` for swap filling, and do not start a second stack or
an extra heavy container on this host. The arithmetic for any host is in
[docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md#2-size-the-vm-so-the-host-keeps-enough-memory).

**Order-book capture is off on every venue** while the platform is built
([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)): `book_capture` in
`config/instruments.yaml` is `false` for all three, so the captures take trades only and the
book tables keep the rows they had, readable, with nothing new arriving. `make health` step (4)
shows what each capture is actually doing; [section 6](#pause-or-resume-one-venues-order-book)
pauses and resumes one venue. **This ends** at P7's release tag or on 2027-02-26, whichever
comes first (the ADR's "Revisit when").

**The ClickHouse hot tier is paused** while the platform is built
([ADR-057](../adr/ADR-057-clickhouse-hot-tier-paused-during-platform-build.md)): the `clickhouse` service is behind the compose profile `hot-gold`, so `make
up` and a bare `docker compose up -d` do not start it; its container is removed and its volumes
are kept. Every command on this page that runs `k2-clickhouse` needs that profile running. The
alerts, dashboards and the `make health` line that read it are removed with it. Starting it again
is `docker compose --profile hot-gold up -d clickhouse` and has a 7-day limit:
[pause-and-resume-a-serving-product.md](../runbooks/pause-and-resume-a-serving-product.md). **This ends** when ADR-057's "Revisit when" fires:
a measured need for sub-second reads, or the release gate.

---

## 1. Start from a fresh clone

**Only on a host with no K2 stack.** Compose names a project after the directory it runs in,
and every K2 container has a fixed name (`container_name: k2-redpanda` and the rest in
`docker-compose.yml`). So a second clone in a directory called `k2-market-data-platform` *is*
the live project (`docker compose config` prints `name: k2-market-data-platform` there, and
`name: operator-docs` in a worktree of that name): `make up` in it, with a freshly generated
`.env`, recreates the containers that read a credential with new ones while the volumes keep the old ones (each
Lakekeeper warehouse stores its own copy of the storage credential), and the lake stops writing.
A clone in a directory of any other name is a second project whose containers collide with the
live ones' names. Check first; this must print nothing:

```bash
docker ps -a --filter name=k2- --format '{{.Names}}'
```

On the reference host it printed 24 names (19 running, 5 exited one-shots) on 2026-11-26; with
`k2-clickhouse` removed by the pause, 23 (18 running). If it prints
anything, the host already has a stack: use section 2, from the directory that stack was
started in.

You need Docker with Compose v2, `git`, `python3`, `jq` (the reads in section 3), and enough
memory: the Docker engine must
offer at least 30 GiB, so that every container's declared memory limit fits (29.812 GiB at the
bootstrap peak, when the one-shots run beside everything else; 37.812 GiB, so 38, with the paused
ClickHouse running; [setup.md](../development/setup.md)),
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
benign start-up noise). These `.env` values are host-specific, not secrets:

- `K2_CAPTURE_CPUSET` and `K2_HEAVY_CPUSET` ship empty: no CPU pinning, starts on any host.
  Set the 15-core layout from `.env.example` only on a host that has those cores, or the
  seven containers that take a cpuset (the three captures on `K2_CAPTURE_CPUSET`; ClickHouse,
  Spark, Trino and the `lake-ddl` one-shot on `K2_HEAVY_CPUSET`) fail at start with
  `invalid argument`.
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

`make health` step (1) makes the same check. Start each service it names with
`docker compose up -d --no-deps <service>`. On 2026-11-25,
after the host killed the VM, it would have named `prometheus`, which stayed `Exited (255)`
despite its restart policy ([docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md)).

**After `docker compose stop`** (section 5), the containers still exist and only need starting:

```bash
docker compose start
```

`start` starts the existing containers as they are and recreates nothing (it may run the
exited one-shots again, which is safe: each is written to be re-run). Not `up -d`: that also
recreates any service whose definition changed since the stop, which is a roll-out, not a
restart. **Not yet run on this stack.** <!-- VERIFY: docker compose start of the whole stack -->

**After `make down`**, the containers are gone and must be created again:

```bash
make up                  # elsewhere; on this host it refuses (Read this first), so instead:
docker compose up -d     # the same compose command make up runs, without the credential check
```

That bare `docker compose up -d` is the one sanctioned use of it on this host, and only to bring
back a stack that already ran with this `.env`: it skips `check-env.py`, which is the guard a
new stack needs. It does not start the paused ClickHouse (profile `hot-gold`). **Not yet run on this stack since the loopback change of 2026-11-26.**
<!-- VERIFY: docker compose up -d after make down -->

**Then, either way:** unpause any DAG you paused before stopping (section 6, step 5; a pause is
kept in Airflow's database across restarts), run `make health` (section 3), and if capture was
down, record the window in `lake.audit.checks` as step 8 of
[docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md) does.

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
(1) long-running services: PASS (every one is running)
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

That run predates step (4), the order-book switch: its lines are tested against a fake
`docker` (`tests/test_health.py`) and not yet recorded from the live stack. It also predates the
ClickHouse pause (ADR-057), which removes the three `(2) <venue>` lines read from ClickHouse.

Line by line (`scripts/health.sh`):

| Line | What it checks | A FAIL or WARN means | Open |
|---|---|---|---|
| `(1) health` | Every running container of this compose project reports `healthy`, or has no healthcheck (`spark-iceberg`, `lake-metrics`). Waits up to 5 min (`K2_HEALTH_WAIT`) | `FAIL (no containers in project '<name>')`: you are not in the live checkout (a git worktree is another compose project), or the stack is down. `FAIL (still unhealthy after 5 min)` lists the containers: read that one's `docker logs --tail 100 k2-<service>` | capture: [capture-down.md](../runbooks/capture-down.md); `redpanda`: [redpanda.md](../runbooks/redpanda.md); `clickhouse` (only while `hot-gold` runs): [failure-recovery.md](../runbooks/failure-recovery.md) section 2; `minio`, `lakekeeper`: [lake-recovery.md](../runbooks/lake-recovery.md); `airflow-*`: [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 1; `trino`: [dbt-marts.md](../runbooks/dbt-marts.md) |
| `(1) long-running services` | Every service `docker-compose.yml` defines without `restart: "no"` has a running container. The health line above cannot see an **exited** container, so this is the line that fails when, for example, Prometheus stayed `Exited (255)` after the 2026-11-25 VM restart | `FAIL (not running: <services>; ...)`: start each by name. `FAIL (could not read the services ...)`: `docker compose config` failed, so nothing is known | `docker compose up -d --no-deps <service>`, then section 2 |
| `(2) <venue>` | **Removed with the ClickHouse pause** (ADR-057); this row describes it as it was. Trades of that venue in ClickHouse `gold.trades` with an exchange timestamp in the last 2 minutes. Never FAIL | `WARN (0 trades/2min)`, or **a venue missing from the list** (only venues with rows print): nothing reached the hot tier from it. `WARN (no rows ...)` for all: ClickHouse unreachable, or nothing is flowing | [capture-feed-stale.md](../runbooks/capture-feed-stale.md); everything silent: [failure-recovery.md](../runbooks/failure-recovery.md) sections 1 and 2 |
| `(2) capture-<venue> metrics` | Reads each capture's `/metrics` (port 8082 inside the compose network, through a throwaway `curlimages/curl` container) and prints the last-message time per stream, in Unix seconds. It does not judge staleness | `WARN (no k2_capture_last_message_ts_seconds yet ...)`: the capture is not serving metrics | [capture-down.md](../runbooks/capture-down.md); staleness per stream: `docker exec k2-capture-<venue> /k2-capture healthcheck` |
| `(3) lake commit age` | Seconds since the newest commit to any lake table, from Prometheus (`time() - max(k2_lake_last_commit_ts_seconds)`), at most 1800 (`K2_HEALTH_LAKE_MAX_AGE`) | `FAIL (Prometheus has no k2_lake_last_commit_ts_seconds ...)`: Prometheus is down (`curl -s localhost:9090/-/healthy`), the `lake-metrics` exporter is down, or the lake never committed. `FAIL (last commit <n>s ago ...)`: the lake is stale | Prometheus down: `docker compose up -d --no-deps prometheus` (safe at any time, section 6); exporter: [lake-recovery.md](../runbooks/lake-recovery.md); stale: [lake-ingest-lag.md](../runbooks/lake-ingest-lag.md) section 2 |
| `(4) <venue> book_capture` | Each capture's `k2_capture_book_capture` (what it read at start, from its `/metrics`) against `book_capture` in the host's `config/instruments.yaml` and in the copy `spark-iceberg` mounts, which the nightly book-audit skip reads ([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)). Tested against a fake `docker` (`tests/test_health.py`); not yet recorded from the live stack | `FAIL (... capture-<venue> was not recreated ...)`: the file changed and the process did not; the line names the recreate. `FAIL (... spark-iceberg's mounted copy is stale ...)`. `FAIL (... exports no k2_capture_book_capture ...)`: an image older than the switch. **During the switch's rollout, or a rollback to the old image, this FAILs for every capture not yet recreated: expected.** `WARN (no metrics ...)`: the capture is down or starting | [Pause or resume one venue's order book](#pause-or-resume-one-venues-order-book), below |
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
errors `binance 0`, `coinbase 0`, `kraken 0`. All 30 alert rules (36 until the six ClickHouse
rules left with the pause, ADR-057), what each means and its runbook: [observability.md](./observability.md#alert-rules).

---

## 4. Consoles and endpoints

Every port is published on `127.0.0.1` only (`docker-compose.yml`, checked by
`scripts/check-budget.py` in CI), so these URLs work on this host and nowhere else; other users
of this host can still reach them ([SECURITY.md](../../SECURITY.md)). Logins are named by the
`.env` variable that holds them; read the value from your `.env`, never from a document. Each
URL answered on 2026-11-26 (`curl -s -o /dev/null -w '%{http_code}'`): 200, except Grafana and
Prometheus, which answer 302 (a redirect to their login and query pages), and the rows noted.

| Service | URL | What it is for | Login |
|---|---|---|---|
| Grafana | http://localhost:3000 | The dashboards: start at **K2 Pipeline Overview**, then **K2 Lake (v3)** and **K2 Resources** (Dashboards, in the left menu; [dashboards.md](./dashboards.md)) | user `admin`, password `GRAFANA_PASSWORD` |
| Airflow | http://localhost:8090 (`K2_AIRFLOW_PORT`) | The four DAGs, their runs, task logs, pause and unpause | user `admin`; password generated by Airflow on first start: `docker exec k2-airflow-api-server cat /opt/airflow/simple_auth_manager_passwords.json.generated` |
| Redpanda Console | http://localhost:8080 | Topics, consumer groups, live messages, decoded as Avro through the schema registry | none |
| Trino | http://localhost:8088/ui/ (`K2_TRINO_PORT`) | SQL over every lake table (catalog `lake`, read-only) and the dbt marts; the UI shows running and recent queries | any user name; not authenticated |
| Lakekeeper | http://localhost:18181/ui/ | Browse the Iceberg catalog: warehouse `k2` holds the lake (`raw`, `bronze`, `silver`, `gold`, `audit`); one warehouse per data domain holds its marts | none (authorization is off) |
| MinIO console | http://localhost:9001 | The objects under the lake, bucket `k2-lake` | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| Prometheus | http://localhost:9090 | Metrics, scrape targets (`/targets`) and alert state (`/alerts`) | none |
| ClickHouse | http://localhost:8123/play | **Paused** (ADR-057): answers only while the `hot-gold` profile runs. A query box over the hot tier, `SELECT count() FROM gold.trades FINAL` | user `default`, `CLICKHOUSE_PASSWORD`; read-only research user `quant`, `K2_QUANT_PASSWORD` |
| Spark master | http://localhost:18080 | Mostly empty by design: the lake jobs run as local drivers and never register with it | none |
| Spark Thrift server | http://localhost:4040 | The UI of the image's always-on Thrift JDBC server (answers 302), **not** of the lake jobs. A running lake job's UI is port 4041 inside the container and is not published: `docker exec k2-spark-iceberg curl -s localhost:4041/api/v1/applications` | none |
| JupyterLab | http://localhost:8889 | The research notebooks; only while `make notebooks` runs in a terminal (answers 000 otherwise) | token printed by `make notebooks` |

APIs rather than consoles, also on `127.0.0.1`: Redpanda's Kafka API `19092` (the listener host
tools use; `9092` is advertised as `redpanda:9092`, for containers), its Admin API `9644` and
Schema Registry `8081`; ClickHouse's native protocol `9002` and its Prometheus endpoint `9363`,
while `hot-gold` runs;
MinIO's S3 API `9000`; PostgreSQL `15432` (`prefect-db`, a historical name: the Airflow and Lakekeeper
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
here. **Not yet run.** <!-- VERIFY: run from a second machine once an SSH server is installed -->

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

**Steps 2 and 3 have not been run as a whole-stack stop on this stack**; the pause and the
wait were run for the 2026-11-25 roll-out. <!-- VERIFY: whole-stack stop -->

Start again with `docker compose start` (section 2), then unpause what you paused.

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
with it; the lake run the kill interrupted was retried by Airflow and succeeded (a lake commit
is all or nothing); Prometheus did not restart by itself. The procedure is [docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md).
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
| `clickhouse` | **paused** (ADR-057): stop and start it only through [pause-and-resume-a-serving-product.md](../runbooks/pause-and-resume-a-serving-product.md). While `hot-gold` runs: any time, with `-t 60`: its Kafka consumers resume from their committed offsets, and duplicates collapse under `FINAL` | hot-tier queries fail while it restarts | `-t 60`, healthy in 8 s |
| `capture-binance`, `capture-kraken`, `capture-coinbase` | any time for correctness, one venue at a time | **that venue's market data while it is down, for good**; a book resync on reconnect | not restarted |
| `redpanda` | with the lake DAGs paused: the ingest reads it | capture holds its records in its producer queue for the restart | `-t 30`, healthy in 13 s; `k2_capture_produce_errors_total` 0 on all three venues before and after |
| `minio`, `lakekeeper`, `spark-iceberg`, `trino`, `prefect-db` | with the four DAGs paused and nothing running (steps 1 and 2 below): every lake and mart write goes through them, and `prefect-db` holds Airflow's and Lakekeeper's databases | a run cut mid-way commits nothing and fails or retries; pausing keeps the record clean | `minio`: healthy in 6 s; `trino` with `--force-recreate`: 10 s; `spark-iceberg`: seconds. `lakekeeper` and `prefect-db` not yet recreated on this stack |
| `airflow-scheduler`, `airflow-api-server`, `airflow-dag-processor` | paused and nothing running: tasks run inside the scheduler and report to the api-server | a running task dies with them | not in this rollout; see [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 3 |
| `airflow-statsd` | as the row above: after recreating it, restart the three Airflow services, which resolve its address only at start | every Airflow metric stops until they restart | [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 3 |

**`prefect-db` will be recreated by the next `docker compose up -d` that includes it.** Since
2026-11-26 its image is pinned by digest (`postgres@sha256:fe0737ba…`, the same 15.19 image the
container already runs), and that changes the service's compose definition, so the first
`docker compose up -d` naming `prefect-db`, or a bare one, or one for a service that depends on
it without `--no-deps` (`airflow-migrate`, `lakekeeper-migrate`), replaces the container of the
catalog database. The data is on the `postgres-data` volume and survives. Every service using
it drops its connection: `lakekeeper` and the three Airflow services. Do it deliberately, with the pattern below (`prefect-db` as `<service>` in
step 3), not as a side effect; until then, start or recreate other services with `--no-deps`.

### The pattern: pause, wait for idle, recreate, verify, unpause

```bash
# 0. Note which DAGs are paused now, so that step 5 unpauses only what you paused.
docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list -o json 2>/dev/null | tail -n 1 \
  | python3 -c 'import json,sys; [print(d["dag_id"], "paused" if d["is_paused"] else "unpaused") for d in json.load(sys.stdin)]'

# 1. Pause all four DAGs; steps 1 and 5 for the first three are as run on 2026-11-25 before
#    the roll-out. Each prints the DAG with its old state (`lake_ingest | False`) when it
#    paused it, and "No unpaused DAGs were found" when it was paused already. lake_rebuild
#    has no schedule, but a run triggered while it is paused waits until the unpause, so
#    nobody starts a rebuild mid-change (step 2 sees such a run and waits).
#    Pause between dbt_marts runs: it starts at minute 10 and took 37 to 74 s on 2026-11-25,
#    and a run paused part-way keeps tasks that never start, which step 2 then waits on.
docker exec k2-airflow-scheduler airflow dags pause lake_ingest
docker exec k2-airflow-scheduler airflow dags pause lake_maintenance
docker exec k2-airflow-scheduler airflow dags pause lake_rebuild
docker exec k2-airflow-scheduler airflow dags pause dbt_marts
```

Step 2 waits until nothing is running, and it **fails closed**: anything it cannot read (a
container that does not answer, output it cannot parse) counts as busy, and at its time limit it
stops with a message and exit status 1, never proceeding. It counts two things as busy:

- **Any Spark driver or Python process in `k2-spark-iceberg`.** Every Spark driver is a JVM
  whose main class is `org.apache.spark.deploy.SparkSubmit` (a PySpark one, started from a
  script, also carries `pyspark-shell`), whatever started it: a lake job, `record_check.py`, a
  chaos script, a notebook kernel, the release gate. The image's always-on Thrift server is one
  too and is left out by its class, `HiveThriftServer2`. A lake script holds the writer lock as
  a Python process before its JVM starts, so Python counts as well, except the image's Jupyter
  server. `docker exec k2-spark-iceberg ps -A -o args=` lists what it reads.
- **Any queued or running run of the four DAGs with a task instance not yet finished**:
  busy unless every task of the run is `success`, `failed`, `skipped`, `upstream_failed` or
  `removed`, so `scheduled`, `queued`, `running`, `up_for_retry`, `deferred`, `restarting`
  and no state at all count as busy, and so does a run with no task yet.

A paused DAG starts none of its unfinished tasks, so a run paused part-way never finishes and
step 2 times out on it (step 1's note on `dbt_marts` is how to avoid that). If it does: unpause
that one DAG, wait until `docker exec k2-airflow-scheduler airflow dags list-runs <dag> --state
running` lists nothing, pause it again, and rerun step 2. Paste the two functions once per
shell; they work in bash and zsh.

```bash
# 2. Wait for idle: polls every 10 s, says what is busy, stops after 15 min with exit 1.
lake_idle() {   # 0 only when every check answered and nothing runs
  local ps_out n dag state runs run
  ps_out=$(docker exec k2-spark-iceberg ps -A -o args= 2>/dev/null) && [ -n "$ps_out" ] \
    || { echo "lake_idle: cannot list processes in k2-spark-iceberg" >&2; return 1; }
  n=$(printf '%s\n' "$ps_out" | grep -v -E 'HiveThriftServer2|jupyter-notebook' \
        | grep -c -E 'org\.apache\.spark\.deploy\.SparkSubmit|pyspark-shell|^[^ ]*python[0-9.]* ')
  [ "$n" = 0 ] || { echo "lake_idle: $n Spark driver or Python process(es) in k2-spark-iceberg" >&2; return 1; }
  for dag in lake_ingest lake_maintenance lake_rebuild dbt_marts; do
    for state in queued running; do
      runs=$(docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list-runs "$dag" --state "$state" -o json 2>/dev/null \
        | tail -n 1 | python3 -c 'import json,sys; print("\n".join(r["run_id"] for r in json.load(sys.stdin)))' 2>/dev/null) \
        || { echo "lake_idle: the Airflow CLI did not answer for $dag" >&2; return 1; }
      while read -r run; do
        [ -n "$run" ] || continue
        docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow tasks states-for-dag-run "$dag" "$run" -o json 2>/dev/null \
          | tail -n 1 | python3 -c 'import json,sys; ts = json.load(sys.stdin); sys.exit(not ts or any(t["state"] not in ("success", "failed", "skipped", "upstream_failed", "removed") for t in ts))' 2>/dev/null \
          || { echo "lake_idle: $dag $run ($state) has a task that has not finished" >&2; return 1; }
      done <<< "$runs"
    done
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

Run 2026-11-26 read-only, with the DAGs unpaused: during a scheduled ingest at 02:11 UTC
`lake_idle` printed `lake_idle: 2 Spark driver or Python process(es) in k2-spark-iceberg` (the
`python3 .../ingest.py` process and its JVM, whose arguments end in `pyspark-shell`) and returned
1; in the gap after it, it returned 0 in bash and in zsh; with the Spark container's name
misspelt it printed `lake_idle: cannot list processes in k2-nope`, and with the scheduler's,
`lake_idle: the Airflow CLI did not answer for lake_ingest`, both returning 1; `wait_idle`
printed `wait_idle: idle at 02:14:32 UTC` after 11 s. The process filter, fed the live listing plus
one synthetic line each, counted a `record_check.py` driver's JVM, a bare `python3` and a
Jupyter kernel as busy, and the Thrift server, the Spark master, worker and history server and
the Jupyter server as idle. Each poll takes 5 to 10 s. Pick a time well clear of 03:00 UTC, when
`lake_maintenance` holds the lake (23.6 minutes on 2026-11-25, up to its 2-hour timeout), and do
not start step 3 unless step 2 printed `idle`.

```bash
# 3. Recreate (or start) the service. Chain it to the wait so a timeout stops it:
wait_idle && docker compose up -d --no-deps <service>                      # definition changed
wait_idle && docker compose up -d --force-recreate --no-deps <service>     # re-read mounts or config
#    with -t 60 for clickhouse and -t 30 for redpanda, as run on 2026-11-25

# 4. Verify, up to 5 minutes: healthy, or running for the two services with no healthcheck
#    (spark-iceberg, lake-metrics). Exit 1 at the limit.
wait_up() {
  local c="k2-$1" st end=$((SECONDS + 300))
  until st=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" 2>/dev/null) \
        && { [ "$st" = healthy ] || [ "$st" = running ]; }; do
    [ "$SECONDS" -lt "$end" ] || { echo "wait_up: $c is '${st:-missing}' after 5 min" >&2; return 1; }
    sleep 5
  done
  echo "wait_up: $c $st"
}
wait_up <service> && make health

# 5. Unpause what step 1 paused, and only that (step 0's list).
docker exec k2-airflow-scheduler airflow dags unpause lake_ingest
docker exec k2-airflow-scheduler airflow dags unpause lake_maintenance
docker exec k2-airflow-scheduler airflow dags unpause lake_rebuild
docker exec k2-airflow-scheduler airflow dags unpause dbt_marts
```

**If step 4 fails, keep the pause.** Read why: `docker logs --tail 100 k2-<service>` and
`docker compose ps -a <service>`. Then roll back: put the previous version of what changed back
and recreate again. After a `git pull`, `ORIG_HEAD` is the commit the pull started from, so
`git checkout ORIG_HEAD -- <path>` restores one path (the checkout is then dirty until the fix
is merged); an image change needs the image rebuilt from that commit. Unpause only once
`make health` passes. **The roll-back is not yet run on this stack.** <!-- VERIFY: roll-back -->

`wait_up` was run read-only on 2026-11-26 against running containers: it printed
`wait_up: k2-spark-iceberg running` and `wait_up: k2-minio healthy`, and for a misspelt name
it printed `wait_up: k2-nope is 'missing' after 5 min` and returned 1 (with its limit cut to
5 s for the test).

**A change that touches several services**: one pause around all of them, and recreate them
one at a time, each verified before the next, in the order used on 2026-11-25: `minio`, then
`trino`, then `spark-iceberg`, `prometheus`, `grafana` and `redpanda-console`, then `clickhouse`
(only while `hot-gold` runs), and `redpanda` last.

Steps 1, 3 and 5 are as run on 2026-11-25 for the loopback roll-out, the timings in the table
above; a later per-minute comparison of lake candles with ClickHouse trades showed no
divergence in that window. The DAGs have `catchup` off (Airflow does not run the slots it
missed while paused), so an unpaused DAG runs its most recent slot at once, then on its
schedule.

**Known gap, tracked here:** the pause and wait in `scripts/chaos/lib.sh`, which the chaos
scripts and `make lake-verify` use, are weaker than step 2. Its `_lake_busy` matches only the
three lake script paths, and its `airflow_task_in_flight` reads an Airflow CLI that does not
answer as "nothing in flight" and counts only `queued` and `running` tasks, so it can report
idle when it cannot tell. Not changed in this pass.

### Pause or resume one venue's order book

The switch is `book_capture` in `config/instruments.yaml`: one YAML `true` or `false` per
venue, required ([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)). `false` means that venue's capture does not subscribe to its
book channel; trades carry on unchanged. A capture reads the file once, at start, and **refuses
to start** if the key is missing or not a boolean, so the order below is not optional. Three
copies of the switch must agree, each one read:

```bash
grep -A3 '^book_capture:' config/instruments.yaml                                     # the file
docker exec k2-spark-iceberg grep -A3 '^book_capture:' /home/iceberg/config/instruments.yaml   # the lake's copy
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=k2_capture_book_capture' \
  | jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'                        # each capture: 1 on, 0 off
make health                                                                           # step (4) compares all three
```

**Why the order matters.** `spark-iceberg` mounts `config/instruments.yaml` as one file, pinned
by its inode, and the captures mount `config/` as a directory, held by all three at once. A
`git pull` replaces the file by renaming a new one over it, and a container recreated while
another still holds the old inode reads the **old** file ([docker/README.md](../../docker/README.md),
"A bind-mounted script runs its old contents"): a new capture binary would then find no
`book_capture` and crash-loop. Writing the new content into the same inode is what every holder
sees at once.

**First rollout of the image that has the switch** (pull request 1 of ADR-050, every venue
`true`), in this order:

```bash
# 1. After the merge: the new registry into the SAME inode, then the pull, then the inode check.
ino=$(stat -c %i config/instruments.yaml)
git fetch origin && git show origin/main:config/instruments.yaml > config/instruments.yaml \
  && git add config/instruments.yaml && git pull --ff-only
[ "$(stat -c %i config/instruments.yaml)" = "$ino" ] && echo "inode kept" || echo "INODE CHANGED: stop"

# 2. What the lake sees: both counts at least 1. Otherwise recreate spark-iceberg and
#    lake-metrics between ingest runs and before 03:00 UTC (the pause pattern above).
docker exec k2-spark-iceberg grep -c book_capture /home/iceberg/config/instruments.yaml /home/iceberg/lake/instruments.py

# 3. Keep the running image for the rollback, then build. No bare `docker compose up -d`
#    until step 4 is done: it would recreate all three captures at once.
docker tag k2-capture:v3 k2-capture:pre-adr050
make build-capture
```

Step 4 recreates **binance, then coinbase, then kraken**, one at a time, each passing all of
this before the next. A recreate loses that venue's trades for the seconds no connection is
open, which is why never two at once:

```bash
# 4. One venue.
v=binance
docker compose up -d --force-recreate --no-deps capture-$v
sleep 10; docker logs k2-capture-$v 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -m1 -E 'book_capture=|missing field'
#    book_capture=true, or: "missing field `book_capture`" means the container read the old file.
#    Then at once: docker tag k2-capture:pre-adr050 k2-capture:v3 and recreate it again (rollback).
wait_up capture-$v                                     # the function in step 4 above: healthy
curl -sG localhost:9090/api/v1/query --data-urlencode "query=k2_capture_book_capture{exchange=\"$v\"}" \
  | jq -r '.data.result[].value[1]'                    # 1
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=time() - k2_capture_last_message_ts_seconds{exchange="binance",stream="pong"}' \
  | jq -r '.data.result[].value[1]'                    # binance only: under 20 (seconds)
```

**The 15-minute gap gate**, before the next venue: for that venue no sequence gap and no
reconnect in 15 minutes, and nothing firing. Fifteen minutes is longer than
`CaptureSequenceGaps`'s 10-minute window plus its 5-minute `for`, so a gap the change caused has
had time to alert. A recreate starts both counters at zero, so any increase is this connection's:

```bash
curl -sG localhost:9090/api/v1/query --data-urlencode "query=sum by (exchange) (increase(k2_capture_gaps_total{exchange=\"$v\"}[15m])) + on (exchange) sum by (exchange) (increase(k2_capture_reconnects_total{exchange=\"$v\"}[15m]))" \
  | jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'      # 0
curl -s localhost:9090/api/v1/alerts | jq -r '.data.alerts[] | "\(.labels.alertname)\t\(.state)"'   # nothing
```

**`make health` step (4) FAILs for every capture not yet recreated during this rollout** (no
gauge yet): that is expected, and the FAIL lines are the list of what is left. The gate is also
where Binance's new liveness is proven: a WebSocket Ping every 15 s, its Pong (or Binance's own
Ping) watched as the `pong` stream at 60 s. A `pong` past 60 s reconnects the session; if that
repeats, roll back.

```bash
# 5. Once all three run the new image: load the alert rules, and check they read the gauge.
docker kill -s HUP k2-prometheus
curl -s localhost:9090/api/v1/rules | jq -r '.data.groups[].rules[]
  | select(.name=="CaptureBookDepthDegraded" or .name=="CaptureChecksumFailure") | "\(.name): \(.query)"'
#    each query ends: unless on (exchange) (k2_capture_book_capture == 0)
make health                                            # step (4): PASS for all three
```

The rules query above was run read-only on 2026-11-26 before this change: it printed both rules
without the gauge, as expected then.

**Rollback**, per venue, any time: `docker tag k2-capture:pre-adr050 k2-capture:v3`, then
`docker compose up -d --force-recreate --no-deps capture-<venue>`, one venue at a time. The old
binary ignores `book_capture` and subscribes to books whatever the file says; the two book
alerts still watch it, because only a gauge reading 0 silences them; `make health` step (4)
FAILs for it ("exports no k2_capture_book_capture") for as long as the rollback lasts. The
registry needs no rollback: maintenance reads the switch without ever stopping on it, and files a
failed `book_capture_switch` row in `lake.audit.checks` when it cannot.

**Later, changing one venue's switch** is the same steps 1, 2 and 4 with no image build: the
change is a pull request carrying the venue's switch and its contracts' `paused` marks together
(`tests/test_contracts.py` fails otherwise), the registry goes in with step 1's in-place write,
and `book_capture=` in step 4's log line and the gauge show the new value. ADR-050 sections 7
and 8 have the order to pause and to resume in, and the operator row that records the window.

**Pausing a venue's book** (pull request 3 of ADR-050 did all three) adds two checks to step 4's.
The gate spans three scheduled ingest runs; on the first venue paused, read the task log for
`stage 2e: 0 rows -> lake.silver.book_<venue>`, because the ingest's empty book path has not run
on this stack before. Then **record the window** in `lake.audit.checks`, as the 2026-11-25
outage was ([failure-recovery.md](../runbooks/failure-recovery.md)): one operator row per venue at
the pause, open-ended, from the receive time of its last book frame (`max(recv_ts)` of
`lake.bronze.binance_depth20`, `kraken_book` or `coinbase_level2` once two ingest runs have
passed the recreate), and a second at the resume with the whole window. `record_check.py` is a
lake job: run it when no ingest runs.

```bash
docker exec -e K2_LAKE_DRIVER_MEMORY=512m k2-spark-iceberg python3 /home/iceberg/lake/record_check.py \
  --job operator --check book_capture_paused --scope "book/$v/<last book frame, UTC>Z.." \
  --observed 0 --failed \
  --detail "L2 not subscribed by decision (ADR-050) from <last book frame>Z; no book data until the closing row"
```

To resume, ADR-050 section 7 has the order (Kraken first, its checksum is the strongest proof
that a venue's format has not drifted) and the pre-flight that comes before it. Roll back a
pause the same way it was made: the switch back to `true`, in place, recreate.

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

Then, for each path that changed. *Under the pause* means section 6's steps 0 to 2 before the
command and step 5 after it.

| Changed | Read by | After the pull |
|---|---|---|
| `docker/lake/*.py` | each lake job, fresh per run | nothing: the next run uses it. `metrics.py` is also the long-running `lake-metrics`: `docker compose up -d --force-recreate --no-deps lake-metrics` |
| `docker/lake/ddl/` (`lake.sql` and the generated table DDL) | the `lake-ddl` one-shot | under the pause: `docker compose up -d --force-recreate --no-deps lake-ddl` |
| `dbt/` models, macros, YAML | the next `dbt_marts` run | nothing |
| `dbt/uv.lock`, `dbt/package-lock.yml`, `docker/airflow/Dockerfile` | baked into the image `k2-airflow:3.3.2` | `docker compose build airflow-migrate` (the only service that builds it), then under the pause `docker compose up -d --force-recreate --no-deps airflow-api-server airflow-scheduler airflow-dag-processor` |
| `docker/airflow/dags/` | the dag-processor | nothing; confirm with `docker exec k2-airflow-scheduler airflow dags list-import-errors` (`No data found`) |
| `docker/airflow/statsd-mapping.yml` | file mount | section 6, the `airflow-statsd` row |
| `docker/prometheus/rules/` | read at start and on SIGHUP (the hang-up signal, which Prometheus takes as "reload your configuration") | `docker kill -s HUP k2-prometheus`, then check http://localhost:9090/rules lists the change |
| `docker/prometheus/prometheus.yml` | file mount | `docker compose up -d --force-recreate --no-deps prometheus` |
| `docker/grafana/dashboards/` | polled every 30 s | nothing |
| `docker/grafana/provisioning/` | read at start | `docker compose up -d --force-recreate --no-deps grafana` |
| `docker/trino/etc/` (including the catalogs `make generate` writes) | read at start | under the pause (section 6, the `trino` row): `docker compose up -d --force-recreate --no-deps trino` |
| `docker/lake/generated/warehouses.txt` (a new data domain from `make generate`) | the `lake-init` one-shot creates its warehouse | `docker compose up -d --force-recreate --no-deps lake-init`, then the `docker/trino/etc/` row; [`docker/trino/README.md`](../../docker/trino/README.md) has the order |
| `docker/clickhouse/config.xml`, `users.xml` | file mounts | while `hot-gold` runs: `docker compose --profile hot-gold up -d --force-recreate --no-deps -t 60 clickhouse`; paused, nothing |
| `docker/clickhouse/ddl/` | only an empty volume | apply by hand: [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md#applying-it-to-a-running-server) |
| `config/instruments.yaml` | the captures at start; file mount in `spark-iceberg` | recreate the three `capture-*` one at a time (each loses its venue's data while down), and `spark-iceberg` under the pause. A `book_capture` change goes in **before** the pull, into the same inode: [Pause or resume one venue's order book](#pause-or-resume-one-venues-order-book) |
| `docker/redpanda/`, `schemas/avro/` | the `redpanda-init` one-shot | `docker compose up -d --force-recreate --no-deps redpanda-init`: every step is create-if-missing or converge |
| `services/capture-rust/` | the image `k2-capture:v3` | `make build-capture`, then recreate the three captures one at a time. The first image with `book_capture` (ADR-050) has its own order: [Pause or resume one venue's order book](#pause-or-resume-one-venues-order-book) |
| `docker/spark/Dockerfile` | the image `k2-spark-iceberg` | `docker compose build spark-iceberg`, then under the pause `docker compose up -d --force-recreate --no-deps spark-iceberg` |
| `docker/lake-metrics/Dockerfile` | the image `k2-lake-metrics` | `docker compose build lake-metrics`, then recreate `lake-metrics` |
| `docker-compose.yml` | compose | `docker compose up -d --no-deps <each changed service>`, under the pause where section 6 asks for one |

Finish with `make health`. A change that touches several rows takes each row's step, the
paused ones inside one pause.

---

## 8. Logs

**Logs are capped.** Each container keeps at most five files of 50 MB (older ones compressed),
about 250 MB, set once for every service in `docker-compose.yml` (`x-logging`). So `docker logs`
reaches back a bounded distance: about two days for the Airflow scheduler, about five weeks for
Redpanda at the rates measured on 2026-11-26, longer for the quiet services. A cap
applies only from the moment a container is recreated; a container still running from before
2026-11-26 has no cap until its next recreate (`docker inspect -f '{{.HostConfig.LogConfig.Config}}' <name>`
shows `map[]` when uncapped). There is no central log store yet.
Because the logging block is part of every service's definition, a bare `docker compose up -d`
after this change recreates every container that has not yet picked it up; recreate services one
at a time with the pattern in section 6.

| What | Where | Kept until |
|---|---|---|
| Any container's output | `docker compose logs --since 10m <service>`, or `docker logs -f k2-<service>`; `make logs` follows all of them | the container is removed (`make down`, a recreate). Save it first: `docker logs k2-<service> > <file> 2>&1` |
| Capture | `docker logs k2-capture-<venue>`; `RUST_LOG` sets the level | as above |
| A lake job's own output (ingest, maintenance, rebuild) | the Airflow task log: the UI, or `docker exec k2-airflow-scheduler sh -c "find /opt/airflow/logs -path '*dag_id=lake_ingest*' -name '*.log' \| sort \| tail -2"` and read the older one; the newest can still be running | the scheduler container is removed |
| dbt runs | the Airflow task logs of `dbt_marts`, the same way | as above |
| Spark job detail | event logs in `/home/iceberg/spark-events` in `k2-spark-iceberg` | `spark-iceberg` is removed |
| ClickHouse server, while `hot-gold` runs | `docker exec k2-clickhouse tail -n 50 /var/log/clickhouse-server/clickhouse-server.err.log` | a volume (`clickhouse-logs`): survives recreates and the pause |
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

## 10. Back up the lake

The raw archive, the audit history and the two dimension histories cannot be rebuilt from
anything, and inside Docker they exist once. `make backup` copies them, as their own Iceberg
files, plus a dump of the catalog database, to `~/k2-backup` on the host, outside Docker; it only
reads from the stack. `make backup-install`, run once from this checkout, schedules it daily at
04:30 UTC as a systemd user timer, and `make health` step (5) then FAILs when the last success
is over 26 hours old (SKIP where it was never set up). The copy is on the same disk and any
process running as you can delete it: it covers a lost Docker volume, a bad command or a
dropped table, not a failed disk. What is copied, what is not, and the restore, not yet
drilled: [lake-backup-and-restore.md](../runbooks/lake-backup-and-restore.md).

**Disk:** `~/k2-backup` is never pruned and grows by every file the lake writes, compacted ones
included: the first copy was 25.3 GiB on 2026-11-26, and a run refuses to start with under
20 GiB free on that disk (`df -h ~`).

---

## Guides

| Doc | What it covers |
|-----|----------------|
| [quick-reference.md](./quick-reference.md) | The per-component commands: Redpanda, ClickHouse (paused), capture, the lake, Trino |
| [data-catalog.md](./data-catalog.md) | Every user-facing table: grain, partitioning, time columns and units, symbol conventions, dedup key, which engine is authoritative, one runnable example each |
| [data-inspection.md](./data-inspection.md) | Operator health queries per hop: Redpanda, ClickHouse `gold` (paused), the Iceberg lake |
| [observability.md](./observability.md) | Grafana dashboards, capture-tier and lake metrics, all 30 Prometheus alert rules |
| [dashboards.md](./dashboards.md) | Every dashboard and console as a screenshot of a live run, in the order the data moves, with what to read from each and the runbook it leads to. Written by `make screenshots` |
| [latency-budgets.md](./latency-budgets.md) | 7-segment latency budget plus the measured 2026-02-19 p50/p99 numbers |
| [docker-resources.md](./docker-resources.md) | Per-service CPU/RAM limits, 15.30 CPU / 27.312 GiB across 18 long-running services, +5 one-shot (2.00 / 2.500 GiB), bootstrap peak 17.30 / 29.812 GiB against the 25 / 48 GiB ceiling, ClickHouse paused and outside them |
| [airflow-schedules.md](./airflow-schedules.md) | The four Airflow DAGs, their crons, limits and runbooks: `lake_ingest`, `lake_maintenance`, `lake_rebuild` and the hourly `dbt_marts` |
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
