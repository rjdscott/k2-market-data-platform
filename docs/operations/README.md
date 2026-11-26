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
(`restart: "no"`: `redpanda-init`, `lakekeeper-migrate`, `lake-init`, `airflow-migrate`); `Exited (0)` is its success state. *Redpanda* is the message broker the
capture processes write every venue message to (it speaks the Kafka protocol). The *hot tier* is
ClickHouse's `gold` database, fed live from Redpanda when it runs; its tables keep duplicate
deliveries until a background merge, and `FINAL` in a query collapses them. A *compose profile*
is a label that keeps a service out of `docker compose up` unless that profile is named. The *lake* is the system of record:
tables in the *Iceberg* format (data files plus metadata that make a set of files one table with
atomic commits), stored in *MinIO* (an S3-compatible object store) and listed by *Lakekeeper*,
the catalog that names each table; a *warehouse* is one named lake in that catalog, `k2` for the
lake and one per data domain for its marts ([glossary](../adr/glossary.md#warehouse-catalog-and-schema)).
No job writes the lake while the platform is rebuilt (below). *Trino* is the SQL engine over the
lake; *dbt* builds *marts*, tables derived from the lake for one use, through Trino
([glossary](../adr/glossary.md#mart)). *Airflow* runs jobs as *DAGs*; a *paused* DAG starts no
new run. A *bind mount* is a file or directory of this checkout mounted into a container, so the
container reads the checkout's copy. *Loopback* is `127.0.0.1`, the address only this host can
reach.

---

## Read this first: the state of this host

**The lake receives no data** while the platform is rebuilt
([ADR-056](../adr/ADR-056-unified-data-product-platform.md)). The v3 lake ingest pipeline (its
Spark jobs, its four Airflow DAGs, its dbt marts, its tables and their alerts) is retired and
archived in [`legacy/v3-lake-ingest/`](../../legacy/v3-lake-ingest/), and its namespaces were
dropped after a copy. The captures keep writing to Redpanda, which keeps 48 hours or 2 GiB per
partition of raw frames, so nothing captured before that window will reach the new lake. Airflow,
Trino, Lakekeeper, MinIO and the Spark container still run, with no DAG and no lake table.
**This ends** when
[phase 004](../plans/2026-11-26-unified-product-platform/004-kraken-trades-instruments-security-master.md)
of the unified platform plan lands `kraken-trades`; phase 002 brings the first DAGs.

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
book topics receive nothing new. `make health` step (4)
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

On the reference host it printed 23 names (18 running, 5 exited one-shots) on 2026-11-26, with
`k2-clickhouse` removed by the pause; once the retirement removes `k2-lake-metrics` and the
`k2-lake-ddl` one-shot, 21 (17 running), by subtraction, not yet read. If it prints
anything, the host already has a stack: use section 2, from the directory that stack was
started in.

You need Docker with Compose v2, `git`, `python3`, `jq` (the reads in section 3), and enough
memory: the Docker engine must
offer at least 29 GiB, so that every container's declared memory limit fits (28.688 GiB at the
bootstrap peak, when the one-shots run beside everything else; 36.688 GiB, so 37, with the paused
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
make up         # check-env again, then docker compose up -d; builds three images on the first run
make health
```

Run 2026-11-26 against a copy of `.env.example`, the fill step left no placeholder and
check-env printed `check-env: PASS ... (MinIO user and password differ; no placeholder secrets)`.
`make up` and `make health` on a fresh clone were last timed on 2026-08-28, about 15 minutes
from nothing to data in every layer of the v3 lake, most of it building the Spark image
([fresh-install.md](../runbooks/fresh-install.md), which also covers the build race and the
benign start-up noise); that lake is retired, so a fresh clone now stops at an empty lake. These `.env` values are host-specific, not secrets:

- `K2_CAPTURE_CPUSET` and `K2_HEAVY_CPUSET` ship empty: no CPU pinning, starts on any host.
  Set the 15-core layout from `.env.example` only on a host that has those cores, or the
  five containers that take a cpuset (the three captures on `K2_CAPTURE_CPUSET`; Spark and
  Trino on `K2_HEAVY_CPUSET`, and the paused ClickHouse with `hot-gold`) fail at start with
  `invalid argument`.
- `K2_AIRFLOW_PORT` (8090) and `K2_TRINO_PORT` (8088) move those two consoles if something
  else on the host owns the port. `K2_DOCKER_GID` is the group that owns the Docker socket, `0`
  on Docker Desktop.

---

## 2. Start a machine that already holds data

All data lives in named Docker volumes (Redpanda, ClickHouse, MinIO for the lake, PostgreSQL
for the Airflow and Lakekeeper metadata, Prometheus, Grafana), which survive stopping, `make
down` and a host reboot. `docker compose up -d` may run the one-shots again; each is written to
be re-run (every step is create-if-missing or converge, per the header of `docker/redpanda/init.sh`
and `docker/lake/init-lake.sh`), so a re-run changes nothing.

**After a host reboot or a Docker Desktop restart**, every long-running service has `restart:
unless-stopped` and comes back by itself, unless you stopped it before. Start Docker Desktop if
it is not running (`systemctl --user start docker-desktop`), then list any long-running service
that did not come back. Run 2026-11-26 on the healthy stack, with the service list read from a
copy of the compose file without the two services the retirement removes, it printed nothing:

```bash
comm -23 <(docker compose config --services | grep -v -x -E 'airflow-migrate|lakekeeper-migrate|lake-init|redpanda-init' | sort) \
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

**Then, either way:** run `make health` (section 3), and if capture was down, record the window
as step 8 of [docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md) says. A DAG paused before
the stop stays paused (Airflow keeps a pause in its database); none exists until phase 002.

---

## 3. Check it is healthy

```bash
make health
```

It took 7 seconds on the healthy stack on 2026-11-26 and up to 5 minutes on a starting one (it waits for
containers to turn healthy). It exits 1 if any line says FAIL; a WARN is "warming up or worth a
look", and a SKIP is a check stood down until a named phase; neither fails it. Steps (1), (2) and
(5) as run 2026-11-26 from the live checkout, with the per-venue record counts replaced because
they are market data:

```console
$ make health
== (1) container health (<= 5 min) ==
(1) health: PASS (all services healthy or have no healthcheck)
(1) long-running services: PASS (every one is running)

== (2) venues, last 2 minutes ==
(2) clickhouse gold.trades: SKIP (the ClickHouse hot tier is paused, ADR-057: not queried; each venue is judged below from its capture and Redpanda's acknowledgements)
(2) binance: PASS (<n> records delivered to Redpanda in 2 min; healthcheck ok: pong is the stalest, 8.8s behind)
(2) kraken: PASS (<n> records delivered to Redpanda in 2 min; healthcheck ok: trade is the stalest, 0.5s behind)
(2) coinbase: PASS (<n> records delivered to Redpanda in 2 min; healthcheck ok: heartbeats is the stalest, 0.2s behind)
...
== (5) backup ==
(5) backup: SKIP (not set up on this host: make backup-install schedules it, docs/runbooks/lake-backup-and-restore.md)
```

Steps (3) and (4) changed with the retirement of the v3 lake: step (3) is a SKIP line naming
phase 004, and step (4) compares two copies of the order-book switch instead of three. Both are
tested against a fake `docker` (`tests/test_health.py`) and not yet recorded from the live stack.

Line by line (`scripts/health.sh`):

| Line | What it checks | A FAIL or WARN means | Open |
|---|---|---|---|
| `(1) health` | Every running container of this compose project reports `healthy`, or has no healthcheck (`spark-iceberg`). Waits up to 5 min (`K2_HEALTH_WAIT`) | `FAIL (no containers in project '<name>')`: you are not in the live checkout (a git worktree is another compose project), or the stack is down. `FAIL (still unhealthy after 5 min)` lists the containers: read that one's `docker logs --tail 100 k2-<service>` | capture: [capture-down.md](../runbooks/capture-down.md); `redpanda`: [redpanda.md](../runbooks/redpanda.md); `clickhouse` (only while `hot-gold` runs): [failure-recovery.md](../runbooks/failure-recovery.md) section 2; `minio`: [failure-recovery.md](../runbooks/failure-recovery.md) section 5; `airflow-*`: [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 1; `lakekeeper`, `trino`: the container's log, and [`docker/trino/README.md`](../../docker/trino/README.md) for Trino |
| `(1) long-running services` | Every service `docker-compose.yml` defines without `restart: "no"` has a running container. The health line above cannot see an **exited** container, so this is the line that fails when, for example, Prometheus stayed `Exited (255)` after the 2026-11-25 VM restart | `FAIL (not running: <services>; ...)`: start each by name. `FAIL (could not read the services ...)`: `docker compose config` failed, so nothing is known | `docker compose up -d --no-deps <service>`, then section 2 |
| `(2) clickhouse gold.trades` | Nothing: a SKIP while the hot tier is paused (ADR-057) | | [pause-and-resume-a-serving-product.md](../runbooks/pause-and-resume-a-serving-product.md) |
| `(2) <venue>` | Two things per venue, both failing: the capture's own healthcheck (every stream within the staleness bound its watchdog and `CaptureFeedStale` share), and the records Redpanda acknowledged from it in the last 2 minutes (`k2_capture_records_delivered_total`, from Prometheus) | `FAIL (capture-<venue> is not running ...)`; `FAIL (capture-<venue> healthcheck: ...)`: a stream is past its bound; `FAIL (Prometheus did not answer ...)` or `FAIL (Prometheus has no k2_capture_records_delivered_total ...)`: delivery is unchecked; `FAIL (0 records delivered to Redpanda in 2 min while the feed is live ...)` | [capture-down.md](../runbooks/capture-down.md); [capture-feed-stale.md](../runbooks/capture-feed-stale.md); Prometheus down: `docker compose up -d --no-deps prometheus` (safe at any time, section 6); [capture-produce-stalled.md](../runbooks/capture-produce-stalled.md) |
| `(3)` | Nothing: a SKIP naming phase 004, when the lake receives data again. Until the retirement it read the lake's newest commit and the last run of the ingest DAG | | |
| `(4) <venue> book_capture` | Each capture's `k2_capture_book_capture` (what it read at start, from its `/metrics`) against `book_capture` in the host's `config/instruments.yaml` ([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)) | `FAIL (... capture-<venue> was not recreated ...)`: the file changed and the process did not; the line names the recreate. `FAIL (... exports no k2_capture_book_capture ...)`: an image older than the switch. `WARN (no metrics ...)`: the capture is down or starting | [Pause or resume one venue's order book](#pause-or-resume-one-venues-order-book), below |
| `(5) backup` | On a host where `make backup-install` ran, the age of the last successful `make backup`; elsewhere a SKIP | `FAIL` past 26 hours, `WARN` when the last run failed | [section 10](#10-back-up-the-lake) |

Beyond `make health`, two reads worth a glance once a day:

```bash
# Nothing firing (prints nothing when no alert is pending or firing)
curl -s localhost:9090/api/v1/alerts | jq -r '.data.alerts[] | "\(.labels.alertname)\t\(.state)"'

# Capture produce errors per venue since each capture started; 0 is the only good value
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=sum by (exchange) (k2_capture_produce_errors_total)' \
  | jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'
```

Run 2026-11-26: the produce errors read `binance 0`, `coinbase 0`, `kraken 0`. The 12 alert rules
that remain (30 until the 14 lake rules and four Airflow rules retired with the v3 lake, 36 before
the six ClickHouse rules left with the pause, ADR-057), what each means and its runbook:
[observability.md](./observability.md#alert-rules).

---

## 4. Consoles and endpoints

Every port is published on `127.0.0.1` only (`docker-compose.yml`, checked by
`scripts/check-budget.py` in CI), so these URLs work on this host and nowhere else; other users
of this host can still reach them ([SECURITY.md](../../SECURITY.md)). Logins are named by the
`.env` variable that holds them; read the value from your `.env`, never from a document. Each
URL answered on 2026-11-26 (`curl -s -o /dev/null -w '%{http_code}'`): 200, except Grafana and
Prometheus, which answer 302 (a redirect to their login and query pages), and the rows noted
(`000` is no answer).

| Service | URL | What it is for | Login |
|---|---|---|---|
| Grafana | http://localhost:3000 | The dashboards: start at **K2 Pipeline Overview**, then **K2 Resources** (Dashboards, in the left menu; [dashboards.md](./dashboards.md)) | user `admin`, password `GRAFANA_PASSWORD` |
| Airflow | http://localhost:8090 (`K2_AIRFLOW_PORT`) | DAGs, their runs, task logs, pause and unpause; no DAG until phase 002 | user `admin`; password generated by Airflow on first start: `docker exec k2-airflow-api-server cat /opt/airflow/simple_auth_manager_passwords.json.generated` |
| Redpanda Console | http://localhost:8080 | Topics, consumer groups, live messages, decoded as Avro through the schema registry | none |
| Trino | http://localhost:8088/ui/ (`K2_TRINO_PORT`) | SQL over the lake (catalog `lake`, read-only) and one catalog per data domain, all without a table until phase 002; the UI shows running and recent queries | any user name; not authenticated |
| Lakekeeper | http://localhost:18181/ui/ | Browse the Iceberg catalog: warehouse `k2` for the lake and one warehouse per data domain, empty since the v3 lake's namespaces were dropped | none (authorization is off) |
| MinIO console | http://localhost:9001 | The objects under the lake, bucket `k2-lake` | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` |
| Prometheus | http://localhost:9090 | Metrics, scrape targets (`/targets`) and alert state (`/alerts`) | none |
| ClickHouse | http://localhost:8123/play | **Paused** (ADR-057): answers only while the `hot-gold` profile runs. A query box over the hot tier, `SELECT count() FROM gold.trades FINAL` | user `default`, `CLICKHOUSE_PASSWORD`; read-only research user `quant`, `K2_QUANT_PASSWORD` |
| Spark History Server | http://localhost:18080 | Finished Spark jobs, newest first, two days of them (its event-log cleaner deletes older ones); open one for its stages. The only process the Spark container runs between jobs. No job runs until phase 002 | none |
| Spark job UI | http://localhost:4040 | The Spark job running now, while it runs: `000` between jobs. A second driver running at the same time gets 4041 inside the container, not published: `docker exec k2-spark-iceberg curl -s localhost:4041/api/v1/applications` | none |

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
# 1. Nothing in flight: section 6 step 1 (wait_idle).

# 2. Stop every container and keep it, with its logs. -t 60 gives each one 60 s to shut
#    down cleanly before Docker kills it (the default is 10 s).
docker compose stop -t 60
```

**Step 2 has not been run as a whole-stack stop on this stack**; step 1 was run read-only on
2026-11-26. <!-- VERIFY: whole-stack stop -->

Start again with `docker compose start` (section 2).

`make down` (`docker compose down`) also stops everything and keeps the volumes, but it
**removes the containers**, and with them three things that live only in a container: every
container's `docker logs`, the Airflow task logs (`/opt/airflow/logs` in `k2-airflow-scheduler`)
and the Spark event logs (`/home/iceberg/spark-events` in `k2-spark-iceberg`). Use it when the
containers or the network must be rebuilt, not to pause the platform.

**Never `docker compose down -v`** on a stack whose data you want: `-v` deletes the volumes,
which are the lake, the hot tier, Redpanda, and the catalog's metadata.

**A hard stop** (the VM or host killed, power lost) loses more than a clean one. Measured
2026-11-25, when the host killed the Docker VM: capture was off 343 s; for the minute of the kill
the hot tier and the v3 lake (both since stood down) held different partial sets of trades,
because Redpanda runs without fsync (`--unsafe-bypass-fsync=true`) and acknowledged records still
in the VM's memory died with it; Prometheus did not restart by itself. The procedure is
[docker-vm-oom-kill.md](../runbooks/docker-vm-oom-kill.md). Until phase 004 nothing reads the
topics, so a long stop costs no more than the market data missed while capture is down: Redpanda
keeps 48 h or 2 GiB per partition of raw frames either way, and phase 004's first run reads from
the start of what it keeps.

---

## 6. Restart or recreate one service

`docker compose restart <service>` restarts the process in the same container. `docker compose
up -d --no-deps <service>` creates a new container if the service's definition changed, and
`--force-recreate` creates one regardless, which is what re-reads a bind-mounted file (see the
rule below). `-t <seconds>` gives the old container that long to stop cleanly.

### Which services need idle

*Idle* means no Spark job in `k2-spark-iceberg` and no Airflow run queued or running. Until phase
002 nothing schedules either, so idle is the normal state, and step 1 below confirms it rather
than waiting for it; the phase that adds DAGs adds their pause to this pattern.

| Service | When it is safe | What a restart costs | As run 2026-11-25, 23:50 to 23:54 UTC |
|---|---|---|---|
| `prometheus`, `grafana`, `redpanda-console`, `cadvisor` | any time | a gap of seconds in scrapes or dashboards; alerts re-evaluate after it | `prometheus`, `grafana`, `redpanda-console`: seconds each |
| `clickhouse` | **paused** (ADR-057): stop and start it only through [pause-and-resume-a-serving-product.md](../runbooks/pause-and-resume-a-serving-product.md). While `hot-gold` runs: any time, with `-t 60`: its Kafka consumers resume from their committed offsets, and duplicates collapse under `FINAL` | hot-tier queries fail while it restarts | `-t 60`, healthy in 8 s |
| `capture-binance`, `capture-kraken`, `capture-coinbase` | any time for correctness, one venue at a time | **that venue's market data while it is down, for good**; a book resync on reconnect | not restarted |
| `redpanda` | any time, with `-t 30`: nothing reads its topics until phase 004 while ClickHouse is paused | capture holds its records in its producer queue for the restart | `-t 30`, healthy in 13 s; `k2_capture_produce_errors_total` 0 on all three venues before and after |
| `minio`, `lakekeeper`, `spark-iceberg`, `trino`, `prefect-db` | when idle (step 1): a Spark job, a Trino query or a catalog call cut mid-way fails, and `prefect-db` holds Airflow's and Lakekeeper's databases | the work in flight fails and must be run again | `minio`: healthy in 6 s; `trino` with `--force-recreate`: 10 s; `spark-iceberg`: seconds. `lakekeeper` and `prefect-db` not yet recreated on this stack |
| `airflow-scheduler`, `airflow-api-server`, `airflow-dag-processor` | when idle: tasks run inside the scheduler and report to the api-server | a running task dies with them | not in this rollout; see [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 1 |
| `airflow-statsd` | as the row above: after recreating it, restart the three Airflow services, which resolve its address only at start | every Airflow metric stops until they restart | [airflow-scheduling.md](../runbooks/airflow-scheduling.md) section 1 |

`make drop` and `make backup` run in throwaway containers of their own, which step 1 does not
see: do not recreate `minio`, `lakekeeper` or `prefect-db` while one of them runs.

**`prefect-db` will be recreated by the next `docker compose up -d` that includes it.** Since
2026-11-26 its image is pinned by digest (`postgres@sha256:fe0737ba…`, the same 15.19 image the
container already runs), and that changes the service's compose definition, so the first
`docker compose up -d` naming `prefect-db`, or a bare one, or one for a service that depends on
it without `--no-deps` (`airflow-migrate`, `lakekeeper-migrate`), replaces the container of the
catalog database. The data is on the `postgres-data` volume and survives. Every service using
it drops its connection: `lakekeeper` and the three Airflow services. Do it deliberately, with the pattern below (`prefect-db` as `<service>` in
step 2), not as a side effect; until then, start or recreate other services with `--no-deps`.

### The pattern: wait for idle, recreate, verify

Step 1 waits until nothing is running, and it **fails closed**: anything it cannot read (a
container that does not answer, output it cannot parse) counts as busy, and at its time limit it
stops with a message and exit status 1, never proceeding. It counts two things as busy:

- **Any Spark driver or Python process in `k2-spark-iceberg`.** Every Spark driver is a JVM
  whose main class is `org.apache.spark.deploy.SparkSubmit` (a PySpark one, started from a
  script, also carries `pyspark-shell`), whatever started it: a job run by hand, a spike, the
  release gate. Between jobs the image runs only tini and the History Server, neither of which
  matches. A Python script counts before its JVM starts.
- **Any queued or running run of any Airflow DAG.** It lists the DAGs first, so it needs no
  change when phase 002 adds them; today the list is empty.

Paste the two functions once per shell; they work in bash and zsh.

```bash
# 1. Wait for idle: polls every 10 s, says what is busy, stops after 15 min with exit 1.
stack_idle() {   # 0 only when every check answered and nothing runs
  local ps_out n dags dag state runs
  ps_out=$(docker exec k2-spark-iceberg ps -A -o args= 2>/dev/null) && [ -n "$ps_out" ] \
    || { echo "stack_idle: cannot list processes in k2-spark-iceberg" >&2; return 1; }
  n=$(printf '%s\n' "$ps_out" | grep -c -E 'org\.apache\.spark\.deploy\.SparkSubmit|pyspark-shell|^[^ ]*python[0-9.]*( |$)')
  [ "$n" = 0 ] || { echo "stack_idle: $n Spark driver or Python process(es) in k2-spark-iceberg" >&2; return 1; }
  dags=$(docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list -o json 2>/dev/null \
    | tail -n 1 | python3 -c 'import json,sys; print("\n".join(d["dag_id"] for d in json.load(sys.stdin)))' 2>/dev/null) \
    || { echo "stack_idle: the Airflow CLI did not answer" >&2; return 1; }
  while read -r dag; do
    [ -n "$dag" ] || continue
    for state in queued running; do
      runs=$(docker exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags list-runs "$dag" --state "$state" -o json 2>/dev/null \
        | tail -n 1 | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null) \
        || { echo "stack_idle: the Airflow CLI did not answer for $dag" >&2; return 1; }
      [ "$runs" = 0 ] || { echo "stack_idle: $dag has $runs $state run(s)" >&2; return 1; }
    done
  done <<< "$dags"
}
wait_idle() {
  local end=$((SECONDS + 900))
  until stack_idle; do
    [ "$SECONDS" -lt "$end" ] || { echo "wait_idle: busy or unreadable after 15 min: recreate nothing" >&2; return 1; }
    sleep 10
  done
  echo "wait_idle: idle at $(date -u +%T) UTC"
}
wait_idle
```

Run 2026-11-26 read-only, in bash and zsh, against the live stack while the old pipeline's
DAGs were still listed: `stack_idle` returned 0 and `wait_idle` printed `wait_idle: idle at
15:01:17 UTC`; polled every 15 s from 15:01 to 15:16 UTC it stayed idle. With the Spark
container's name misspelt it printed `stack_idle: cannot list processes in k2-nope`, and with
the scheduler's, `stack_idle: the Airflow CLI did not answer`, both returning 1. Against a fake
`docker` answering one DAG with one running run it printed `stack_idle: product_a has 1 running
run(s)`, and with a Python job in the Spark listing `stack_idle: 1 Spark driver or Python
process(es) in k2-spark-iceberg`, both returning 1. The process filter, fed the History Server's
two lines plus synthetic ones, counted a Python job, a SparkSubmit driver, a bare `python3` and a
`python3.10` kernel as busy and the `sh -c` wrapper and `ps` itself as idle. In a throwaway
Airflow 3.3.2 with no DAG, `airflow dags list -o json` printed `[]`, so the DAG loop has nothing
to read today. Each poll takes 5 to 15 s; do not start step 2 unless step 1 printed `idle`.

```bash
# 2. Recreate (or start) the service. Chain it to the wait so a timeout stops it:
wait_idle && docker compose up -d --no-deps <service>                      # definition changed
wait_idle && docker compose up -d --force-recreate --no-deps <service>     # re-read mounts or config
#    with -t 60 for clickhouse and -t 30 for redpanda, as run on 2026-11-25

# 3. Verify, up to 5 minutes: healthy, or running for spark-iceberg, which has no
#    healthcheck. Exit 1 at the limit.
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
```

**If step 3 fails**, read why: `docker logs --tail 100 k2-<service>` and
`docker compose ps -a <service>`. Then roll back: put the previous version of what changed back
and recreate again. After a `git pull`, `ORIG_HEAD` is the commit the pull started from, so
`git checkout ORIG_HEAD -- <path>` restores one path (the checkout is then dirty until the fix
is merged); an image change needs the image rebuilt from that commit. **The roll-back is not yet
run on this stack.** <!-- VERIFY: roll-back -->

`wait_up` was run read-only on 2026-11-26 against running containers, in bash and zsh: it printed
`wait_up: k2-spark-iceberg running`, `wait_up: k2-minio healthy` and `wait_up: k2-lakekeeper
healthy`, and for a misspelt name it printed `wait_up: k2-nope is 'missing' after 5 min` and
returned 1 (with its limit cut to 5 s for the test).

**A change that touches several services**: recreate them one at a time, each verified before the
next, in the order used on 2026-11-25: `minio`, then `trino`, then `spark-iceberg`, `prometheus`,
`grafana` and `redpanda-console`, then `clickhouse` (only while `hot-gold` runs), and `redpanda`
last. Steps 2 and 3 are as run on 2026-11-25 for the loopback roll-out, the timings in the table
above.

### Pause or resume one venue's order book

The switch is `book_capture` in `config/instruments.yaml`: one YAML `true` or `false` per
venue, required ([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)). `false` means that venue's capture does not subscribe to its
book channel; trades carry on unchanged. A capture reads the file once, at start, and **refuses
to start** if the key is missing or not a boolean, so the order below is not optional. Two
copies of the switch must agree, each one read:

```bash
grep -A3 '^book_capture:' config/instruments.yaml                                     # the file
curl -sG localhost:9090/api/v1/query --data-urlencode 'query=k2_capture_book_capture' \
  | jq -r '.data.result[] | "\(.metric.exchange) \(.value[1])"'                        # each capture: 1 on, 0 off
make health                                                                           # step (4) compares the two
```

`spark-iceberg` still mounts a copy, which nothing reads since the v3 lake's book audit retired.

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

# 2. Retired with the v3 lake: nothing reads the Spark container's copy any more.

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

The rules query above was run read-only on 2026-11-26, after the rollout: both queries end with
the `unless` clause. The gap gate's query and the `pong` age were run read-only the same day.

**Rollback**, per venue, any time: `docker tag k2-capture:pre-adr050 k2-capture:v3`, then
`docker compose up -d --force-recreate --no-deps capture-<venue>`, one venue at a time. The old
binary ignores `book_capture` and subscribes to books whatever the file says; the two book
alerts still watch it, because only a gauge reading 0 silences them; `make health` step (4)
FAILs for it ("exports no k2_capture_book_capture") for as long as the rollback lasts. The
registry needs no rollback: nothing but the captures reads the switch since the v3 lake retired.

**Later, changing one venue's switch** is the same steps 1 and 4 with no image build: the
change is a pull request carrying the venue's switch, the registry goes in with step 1's in-place
write, and `book_capture=` in step 4's log line and the gauge show the new value. ADR-050
sections 7 and 8 have the order to pause and to resume in.

**Pausing a venue's book** adds no lake check any more: the v3 lake's book tables, the ingest
log line that showed a paused venue's empty book path, and the operator row that recorded each
pause window retired with that lake (archived in `legacy/v3-lake-ingest/`). Write the window,
from the receive time of the venue's last book frame, in the pull request that carries the
switch.

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
sha256sum docker/lake/offsets.py; docker exec k2-spark-iceberg sha256sum /home/iceberg/lake/offsets.py
```

---

## 7. Roll out a merged change

The live checkout is bind-mounted into the running containers, so **a `git pull` in the live
checkout is a deploy** for everything that reads its files at run time: a Spark job started
in `k2-spark-iceberg` runs the new `docker/lake/` code, and the dag-processor re-reads a changed
DAG file within 30 s (`min_file_process_interval`). There is no separate release step, so pull
only what you mean to run; phase 001 replaces this with deploys from releases.

```bash
git status --short                         # must print nothing: a pull onto local edits is not a deploy of main
git fetch origin
git log --oneline HEAD..origin/main        # what will deploy
git diff --name-only HEAD origin/main      # which paths: find each in the table below
git pull --ff-only
```

Then, for each path that changed. *When idle* means section 6's step 1 before the command.

| Changed | Read by | After the pull |
|---|---|---|
| `docker/lake/*.py` | a Spark job, fresh per run; none is scheduled until phase 002 | nothing |
| `docker/lake/ddl/` | nothing: the `lake-ddl` one-shot that applied it retired with the v3 lake | nothing |
| `dbt/` models, macros, YAML | nothing runs dbt on the stack until phase 002 | nothing |
| `dbt/uv.lock`, `dbt/package-lock.yml`, `docker/airflow/Dockerfile` | baked into the image `k2-airflow:3.3.2` | `docker compose build airflow-migrate` (the only service that builds it), then when idle `docker compose up -d --force-recreate --no-deps airflow-api-server airflow-scheduler airflow-dag-processor` |
| `docker/airflow/dags/` | the dag-processor | nothing; confirm with `docker exec k2-airflow-scheduler airflow dags list-import-errors` (`No data found`) |
| `docker/airflow/statsd-mapping.yml` | file mount | section 6, the `airflow-statsd` row |
| `docker/prometheus/rules/` | read at start and on SIGHUP (the hang-up signal, which Prometheus takes as "reload your configuration") | `docker kill -s HUP k2-prometheus`, then check http://localhost:9090/rules lists the change |
| `docker/prometheus/prometheus.yml` | file mount | `docker compose up -d --force-recreate --no-deps prometheus` |
| `docker/grafana/dashboards/` | polled every 30 s | nothing |
| `docker/grafana/provisioning/` | read at start | `docker compose up -d --force-recreate --no-deps grafana` |
| `docker/trino/etc/` (including the catalogs `make generate` writes) | read at start | when idle (section 6, the `trino` row): `docker compose up -d --force-recreate --no-deps trino` |
| `docker/lake/generated/warehouses.txt` (a new data domain from `make generate`) | the `lake-init` one-shot creates its warehouse | `docker compose up -d --force-recreate --no-deps lake-init`, then the `docker/trino/etc/` row; [`docker/trino/README.md`](../../docker/trino/README.md) has the order |
| `docker/clickhouse/config.xml`, `users.xml` | file mounts | while `hot-gold` runs: `docker compose --profile hot-gold up -d --force-recreate --no-deps -t 60 clickhouse`; paused, nothing |
| `docker/clickhouse/ddl/` | only an empty volume | apply by hand: [`docker/clickhouse/README.md`](../../docker/clickhouse/README.md#applying-it-to-a-running-server) |
| `config/instruments.yaml` | the captures at start; file mount in `spark-iceberg`, which nothing reads now | recreate the three `capture-*` one at a time (each loses its venue's data while down), and `spark-iceberg` when idle to keep its copy current. A `book_capture` change goes in **before** the pull, into the same inode: [Pause or resume one venue's order book](#pause-or-resume-one-venues-order-book) |
| `docker/redpanda/`, `schemas/avro/` | the `redpanda-init` one-shot | `docker compose up -d --force-recreate --no-deps redpanda-init`: every step is create-if-missing or converge |
| `services/capture-rust/` | the image `k2-capture:v3` | `make build-capture`, then recreate the three captures one at a time. The first image with `book_capture` (ADR-050) has its own order: [Pause or resume one venue's order book](#pause-or-resume-one-venues-order-book) |
| `docker/spark/Dockerfile` | the image `k2-spark-iceberg` | `docker compose build spark-iceberg`, then when idle `docker compose up -d --force-recreate --no-deps spark-iceberg` |
| `docker-compose.yml` | compose | `docker compose up -d --no-deps <each changed service>`, when idle where section 6 asks for it. A service the file no longer defines is removed only by `docker compose up -d --remove-orphans` |

Finish with `make health`. A change that touches several rows takes each row's step, one
service at a time.

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
| A DAG task's own output (none until phase 002) | the Airflow task log: the UI, or `docker exec k2-airflow-scheduler sh -c "find /opt/airflow/logs -path '*dag_id=<dag>*' -name '*.log' \| sort \| tail -2"` and read the older one; the newest can still be running. The logs of the retired v3 DAGs stay there until the container is removed | the scheduler container is removed |
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
5. Before any repair that deletes or rewrites data, read the runbook to the end: a data gap is
   usually something to record, not to fix, because the venues' feeds do not replay
   ([runbook triage](../runbooks/README.md#triage)).

---

## 10. Back up the lake

`make backup` copies what nothing can rebuild, as its own Iceberg files plus a dump of the
catalog database, to `~/k2-backup` on the host, outside Docker; it only reads from the stack.
The four tables it copied (the raw archive, the audit history and two dimension histories)
retired with the v3 lake, so until
[phase 002](../plans/2026-11-26-unified-product-platform/002-run-spine-through-ecb-fx.md)
compiles the backup set from each product's contract it has no table to copy, and says so with a
SKIP line naming that phase. The copies it took before stay in `~/k2-backup/lake/`, and the copies
`make drop` took of the dropped namespaces in `~/k2-backup/drop/<date>/`. `make backup-install`,
run once from this checkout, schedules it daily at 04:30 UTC as a systemd user timer, and
`make health` step (5) then FAILs when the last success is over 26 hours old (SKIP where it was
never set up, as on this host on 2026-11-26). The copy is on the same disk and any process
running as you can delete it: [lake-backup-and-restore.md](../runbooks/lake-backup-and-restore.md).

**Disk:** `~/k2-backup` is never pruned: the first copy was 25.3 GiB on 2026-11-26, and a run
refuses to start with under 20 GiB free on that disk (`df -h ~`).

---

## Guides

| Doc | What it covers |
|-----|----------------|
| [quick-reference.md](./quick-reference.md) | The per-component commands: Redpanda, ClickHouse (paused), capture, the lake catalog, Trino |
| [data-catalog.md](./data-catalog.md) | **Archived** with the v3 lake ingest pipeline: what replaces it, and where it is kept |
| [data-inspection.md](./data-inspection.md) | **Archived** with the v3 lake ingest pipeline: what replaces it, and where it is kept |
| [observability.md](./observability.md) | Grafana dashboards, capture-tier metrics, the 12 Prometheus alert rules |
| [dashboards.md](./dashboards.md) | Every dashboard and console as a screenshot of a live run, in the order the data moves, with what to read from each and the runbook it leads to. Written by `make screenshots` |
| [latency-budgets.md](./latency-budgets.md) | 7-segment latency budget plus the measured 2026-02-19 p50/p99 numbers |
| [docker-resources.md](./docker-resources.md) | Per-service CPU/RAM limits, 15.20 CPU / 27.188 GiB across 17 long-running services, +4 one-shot (1.50 / 1.500 GiB), bootstrap peak 16.70 / 28.688 GiB against the 25 / 48 GiB ceiling, ClickHouse paused and outside them |
| [airflow-schedules.md](./airflow-schedules.md) | **Archived** with the v3 lake ingest pipeline, whose four DAGs it listed |
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
- [Decisions](../adr/README.md), ADR-001 to ADR-057
- [Development](../development/setup.md), the inner loops for changing Rust or Python code, and [testing](../development/testing.md)
