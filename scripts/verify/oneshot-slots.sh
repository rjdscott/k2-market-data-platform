#!/usr/bin/env bash
# The one-shot operator (docker/airflow/dags/oneshot.py) on a throwaway Airflow (step W0-8): four
# LocalExecutor slots and the pools lake_spark 1, fetch 1, dbt 2 hold; a task killed in Airflow
# leaves no container; a crashed try's container is removed by the retry; and the operator's
# mounts work on this Docker host, fetch's spool volume subpath (its DAG's directory alone) among them. One PASS or FAIL line per check, then `<n> FAIL`; exit 0 only
# when none failed.
#
#   bash scripts/verify/oneshot-slots.sh
#
# A compose project of its own, `oneslots-<tag>` (postgres and `airflow standalone` from the
# stack's k2-airflow:3.3.2 image, LocalExecutor with parallelism 4, the Docker socket), a scratch
# directory under $HOME (Docker Desktop shares the home directory, not /tmp) holding a release
# with compiled/oneshots.json, dummy secret files (mode 600) and the DAGs, and one image built
# from a pinned alpine with USER 10001 and /spool owned by it, tagged as each kind. Every DAG id,
# and so every one-shot's name, carries <tag>. The trap removes the project's containers,
# volumes and networks, every one-shot named with <tag>, the spool volume, the image and its
# tags, and the scratch directory. Touches nothing else: not the live stack, not k2-rehearsal.
#
# The DAGs, one task each (one task per kind per DAG, as the factory will have it):
#   spark1, spark2  tools in pool lake_spark, 25 s       fetch1, fetch2  fetch in pool fetch, 25 s
#   dbt1, dbt2      dbt-runner in pool dbt, 25 s         extra           tools in default_pool, 25 s,
#                                                                         the lowest priority
#   kill            fetch, 120 s, marked failed through the REST API once its container runs
#   crash           fetch, retries 1: 240 s on try 1, the Airflow container killed under it
#                   and started again; 5 s on try 2. Airflow resets a dead scheduler's running
#                   task only from its orphan check, every orphaned_tasks_check_interval (300 s by
#                   default, 10 s here) once the dead job's heartbeat is 30 s old.
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$PWD
START=$SECONDS

avail=$(awk '/MemAvailable/ {print int($2 / 1048576)}' /proc/meminfo)
[ "$avail" -ge 8 ] || { echo "oneshot-slots: MemAvailable ${avail} GiB, under 8 GiB: not starting" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "oneshot-slots: no Docker" >&2; exit 2; }
# One container job on this host at a time: anything running that is not the stack's is another's.
others=$(docker ps --format '{{.Names}}' | grep -v '^k2-' || true)
[ -z "$others" ] || { echo "oneshot-slots: another container job is running ($others): not starting" >&2; exit 2; }

TAG="w08$(date +%s | tail -c 5)$((RANDOM % 1000))"
P="oneslots-$TAG"
AF="$P-airflow"
SPOOL="${P}_k2-spool"
RID="v0.0.0-oneslots-0123456789ab"
ALPINE='alpine@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf'
POSTGRES='postgres@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b'
S=$(mktemp -d "$HOME/.oneshot-slots.XXXXXX")
IMG="oneslots-$TAG:img"
TAGS=()

cleanup() {
  set +e
  ids=$(docker ps -aq --filter "name=-${TAG}_")  # first: a one-shot still on a network holds it
  [ -n "$ids" ] && docker rm -f $ids >/dev/null 2>&1
  docker compose -p "$P" -f "$S/compose.yml" down -v --remove-orphans -t 5 >/dev/null 2>&1
  docker volume rm "$SPOOL" >/dev/null 2>&1
  [ ${#TAGS[@]} -gt 0 ] && docker rmi "${TAGS[@]}" >/dev/null 2>&1
  docker rmi "$IMG" >/dev/null 2>&1
  rm -rf "$S"
  left=$(docker ps -aq --filter "name=$TAG"; docker volume ls -q --filter "name=$P"; docker network ls -q --filter "name=$P")
  [ -z "$left" ] || echo "oneshot-slots: teardown left: $left" >&2
}
trap cleanup EXIT

# The image: every kind's tag points at it, so `--pull never` finds each.
printf 'FROM %s\nLABEL com.k2.verify=oneshot-slots-%s\nRUN mkdir /spool && chown 10001:10001 /spool\nUSER 10001:10001\n' \
  "$ALPINE" "$TAG" | docker build -q -t "$IMG" - >/dev/null
ID12=$(docker image inspect -f '{{.Id}}' "$IMG" | sed 's/^sha256://' | cut -c1-12)
for kind in fetch dbt-runner tools; do docker tag "$IMG" "$kind:$ID12"; TAGS+=("$kind:$ID12"); done
docker volume create "$SPOOL" >/dev/null  # empty, as compose would create it for its first reader

mkdir -p "$S/releases/$RID/compiled" "$S/dags" "$S/secrets/principals" "$S/secrets/tls"
chmod 755 "$S" "$S/releases" "$S/releases/$RID" "$S/releases/$RID/compiled" "$S/dags"
python3 - "$REPO/platform/k2gen/tests/golden/pipeline/compiled/oneshots.json" "$ID12" \
  "$S/releases/$RID/compiled/oneshots.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
for kind, spec in doc["oneshots"].items():
    spec["image"] = f"{kind}:{sys.argv[2]}"
open(sys.argv[3], "w").write(json.dumps(doc, indent=2) + "\n")
PY
chmod 644 "$S/releases/$RID/compiled/oneshots.json"
for f in principals/dbt_w08.password principals/pub_w08.password principals/svc.runs.password tls/ca.pem; do
  echo "dummy, not a secret" > "$S/secrets/$f"; chmod 600 "$S/secrets/$f"
done
chmod 700 "$S/secrets" "$S/secrets/principals" "$S/secrets/tls"
cp docker/airflow/dags/oneshot.py "$S/dags/"
cat > "$S/dags/slots.py" <<PY
from datetime import timedelta

import pendulum
from airflow.sdk import DAG
from oneshot import OneShotOperator

RELEASE = "/k2/releases/$RID"
HAS_RELEASE = "test -f /k2/releases/$RID/compiled/oneshots.json"
# fetch sees /spool/<dagId> alone (fetch2 runs after fetch1, whose directory is then in the volume)
FETCH = (HAS_RELEASE + ' && test "\$(ls -A /spool)" = "{{ dag.dag_id }}"'
         ' && d="/spool/{{ dag.dag_id }}/{{ run_id }}" && mkdir -p "\$d" && id -u > "\$d/uid" && ')
DBT = ("cat /run/k2-secrets/builder.password /run/k2-secrets/publisher.password /run/k2-secrets/runs.password "
       "/run/k2-secrets/tls/ca.pem > /dev/null && test \"\$(id -u)\" = $(id -u) && " + HAS_RELEASE +
       " && sleep 25 && echo '{\"exit\": 0, \"expire\": []}'")
TASKS = {
    "spark1": ("tools", "lake_spark", HAS_RELEASE + " && sleep 25", 10, 0),
    "spark2": ("tools", "lake_spark", HAS_RELEASE + " && sleep 25", 10, 0),
    "fetch1": ("fetch", "fetch", FETCH + "sleep 25", 10, 0),
    "fetch2": ("fetch", "fetch", FETCH + "sleep 25", 10, 0),
    "dbt1": ("dbt-runner", "dbt", DBT, 10, 0),
    "dbt2": ("dbt-runner", "dbt", DBT, 10, 0),
    "extra": ("tools", "default_pool", HAS_RELEASE + " && sleep 25", 1, 0),
    "kill": ("fetch", "default_pool", FETCH + "sleep 120", 10, 0),
    "crash": ("fetch", "default_pool", FETCH + "sleep {{ 240 if ti.try_number == 1 else 5 }}", 10, 1),
}
for name, (kind, pool, command, priority, retries) in TASKS.items():
    with DAG("${TAG}_" + name, schedule=None, start_date=pendulum.datetime(2026, 1, 1, tz="UTC"), catchup=False,
             max_active_runs=1) as dag:
        OneShotOperator(task_id=name, kind=kind, pool=pool, release_path=RELEASE, command=["sh", "-c", command],
                        priority_weight=priority, weight_rule="absolute", retries=retries,
                        retry_delay=timedelta(seconds=5),
                        env={"K2_TRINO_USER": "dbt_w08", "K2_PUB_USER": "pub_w08"} if kind == "dbt-runner" else None,
                        principals=["dbt_w08", "pub_w08", "svc.runs"] if kind == "dbt-runner" else None)
    globals()[dag.dag_id] = dag
PY
secret() { python3 -c 'import secrets; print(secrets.token_hex(16))'; }
DBPW=$(secret)
cat > "$S/compose.yml" <<YML
networks:
  k2-net: {}
  k2-catalog: {}
services:
  db:
    image: $POSTGRES
    environment: {POSTGRES_USER: airflow, POSTGRES_PASSWORD: "$DBPW", POSTGRES_DB: airflow}
    networks: [k2-net]
    healthcheck: {test: ["CMD", "pg_isready", "-U", "airflow"], interval: 2s, retries: 30}
    deploy: {resources: {limits: {cpus: "0.5", memory: 512M}}}
  airflow:
    image: k2-airflow:3.3.2
    container_name: $AF
    command: ["standalone"]
    depends_on: {db: {condition: service_healthy}}
    networks: [k2-net, k2-catalog]
    group_add: ["${K2_DOCKER_GID:-0}"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - $S/dags:/opt/airflow/dags:ro
      - $S/releases:/k2/releases:ro
    environment:
      AIRFLOW__CORE__EXECUTOR: LocalExecutor
      AIRFLOW__CORE__PARALLELISM: "4"
      AIRFLOW__CORE__LOAD_EXAMPLES: "False"
      AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION: "False"
      AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS: "True"
      AIRFLOW__DATABASE__SQL_ALCHEMY_CONN: postgresql+psycopg2://airflow:$DBPW@db:5432/airflow
      AIRFLOW__CORE__EXECUTION_API_SERVER_URL: http://localhost:8080/execution/
      AIRFLOW__API_AUTH__JWT_SECRET: "$(secret)"
      AIRFLOW__API__SECRET_KEY: "$(secret)"
      AIRFLOW__DAG_PROCESSOR__REFRESH_INTERVAL: "5"
      AIRFLOW__SCHEDULER__TASK_INSTANCE_HEARTBEAT_TIMEOUT: "20"
      AIRFLOW__SCHEDULER__TASK_INSTANCE_HEARTBEAT_TIMEOUT_DETECTION_INTERVAL: "5"
      # A dead scheduler's running tasks are reset only by the orphan check (default every 300 s),
      # once its job's heartbeat is scheduler_health_check_threshold (30 s) old: the heartbeat
      # timeout above applies only to tasks the live scheduler queued.
      AIRFLOW__SCHEDULER__ORPHANED_TASKS_CHECK_INTERVAL: "10"
      K2_RELEASES: $S/releases
      K2_SECRETS: $S/secrets
      K2_COMPOSE_PROJECT: $P
      K2_RUN_UID: "$(id -u)"
      K2_RUN_GID: "$(id -g)"
      K2_TRINO_HOST: trino
      K2_TRINO_INTERNAL_PORT: "8443"
      K2_RUNS_USER: svc.runs
      K2_CATALOG_URI: http://lakekeeper:8181/catalog
    deploy: {resources: {limits: {cpus: "2", memory: 3G}}}
YML

docker compose -p "$P" -f "$S/compose.yml" up -d --quiet-pull >"$S/up.log" 2>&1 || { cat "$S/up.log" >&2; exit 1; }
code=0
python3 - "$AF" "$TAG" "$SPOOL" "$IMG" "$(id -u):$(id -g)" <<'PY' || code=$?
import json, subprocess, sys, time
from urllib.parse import quote

AF, TAG, SPOOL, IMG, USER = sys.argv[1:]
fails = 0


def check(ok, line, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} {line}" + (f" ({detail})" if detail else ""), flush=True)


def api(method, path, body=None):
    cmd = ["docker", "exec", AF, "curl", "-s", "-X", method, f"http://localhost:8080/api/v2/{path}",
           "-H", "Content-Type: application/json", "-w", "\n%{http_code}"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    text, _, status = out.rpartition("\n")
    try:
        return int(status or 0), json.loads(text)
    except ValueError:
        return int(status or 0), text


def alive():
    out = subprocess.run(["docker", "ps", "--filter", f"name=-{TAG}_", "--format", "{{.Names}}"],
                         capture_output=True, text=True).stdout
    return set(out.split())


def name(kind, dag, attempt=1):
    return f"k2-{kind}-{TAG}_{dag}-{attempt}"


def wait(what, until, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if result := until():
            return result
        time.sleep(1)
    raise TimeoutError(f"{what}: not within {timeout} s")


def healthy():
    return api("GET", "monitor/health")[0] == 200


def states(dags):
    status, body = api("POST", "dags/~/dagRuns/~/taskInstances/list",
                       {"dag_ids": [f"{TAG}_{d}" for d in dags], "page_limit": 100})
    return {t["task_id"]: t for t in body.get("task_instances", [])} if status == 200 else {}


def trigger(dag):
    status, body = api("POST", f"dags/{TAG}_{dag}/dagRuns", {"logical_date": None})
    if status != 200:
        raise RuntimeError(f"trigger {dag}: {status} {body}")
    return body["dag_run_id"]


def log(dag, attempt=1):
    return subprocess.run(["docker", "exec", AF, "sh", "-c",
                           f"cat /opt/airflow/logs/dag_id={TAG}_{dag}/*/task_id={dag}/attempt={attempt}.log"],
                          capture_output=True, text=True).stdout


try:
    wait("the api-server", healthy, 300)
    for pool, slots in (("lake_spark", 1), ("fetch", 1), ("dbt", 2)):
        api("POST", "pools", {"name": pool, "slots": slots, "description": "oneshot-slots"})
    pools = {p["name"]: p["slots"] for p in api("GET", "pools")[1].get("pools", [])}
    check(all(pools.get(p) == n for p, n in (("lake_spark", 1), ("fetch", 1), ("dbt", 2))),
          "pools lake_spark 1, fetch 1, dbt 2", str({p: pools.get(p) for p in ("lake_spark", "fetch", "dbt")}))
    wave = ["spark1", "spark2", "fetch1", "fetch2", "dbt1", "dbt2", "extra"]
    every = wave + ["kill", "crash"]

    def parsed():
        status, body = api("GET", f"dags?dag_id_pattern={TAG}&limit=100")
        return status == 200 and body["total_entries"] == len(every)
    try:
        wait("the DAGs parsed", parsed, 180)
    except TimeoutError:
        print(api("GET", "importErrors")[1])
        raise

    # Slots and pools: seven tasks, four slots.
    runs = {d: trigger(d) for d in wave}
    snaps, extra_at_four, t0 = [], None, time.monotonic()
    while time.monotonic() - t0 < 240:
        snap = alive()
        snaps.append(snap)
        if len(snap) == 4 and extra_at_four is None:
            extra_at_four = (name("tools", "extra") in snap, states(["extra"]).get("extra", {}).get("state"))
        if len(snaps) % 5 == 0:
            st = states(wave)
            if len(st) == len(wave) and all(t["state"] in ("success", "failed", "upstream_failed") for t in st.values()):
                break
        time.sleep(1)
    final = states(wave)
    peak = max(len(s) for s in snaps)
    check(peak == 4, "four LocalExecutor slots: at most 4 one-shots ran at once, and 4 did", f"peak {peak}")
    for kind, a, b in (("tools", "spark1", "spark2"), ("fetch", "fetch1", "fetch2")):
        both = any(name(kind, a) in s and name(kind, b) in s for s in snaps)
        ran = all(any(name(kind, d) in s for s in snaps) for d in (a, b))
        check(ran and not both, f"pool of 1: {a} and {b} both ran and never at once")
    check(any(name("dbt-runner", "dbt1") in s and name("dbt-runner", "dbt2") in s for s in snaps),
          "pool dbt 2: dbt1 and dbt2 ran at once")
    check(extra_at_four is not None and not extra_at_four[0] and extra_at_four[1] in ("scheduled", "queued"),
          "a fifth task queues on the slots: extra (default_pool, free) waits while four run",
          f"extra at four: {extra_at_four}")
    bad = {d: t["state"] for d, t in final.items() if t["state"] != "success"}
    check(len(final) == len(wave) and not bad, "all seven succeeded", str(bad or ""))
    for d in bad:
        print(f"  {d} log tail: {log(d)[-600:]!r}")
    status, x = api("GET", f"dags/{TAG}_dbt1/dagRuns/{quote(runs['dbt1'], safe='')}/taskInstances/dbt1/xcomEntries/result")
    check(status == 200 and "expire" in str(x.get("value") if isinstance(x, dict) else x),
          "dbt-runner's last stdout line is XCom result", f"{status} {x.get('value') if isinstance(x, dict) else x}")
    check(final.get("dbt1", {}).get("state") == "success",
          f"--user {USER}: dbt-runner read its mode-600 secret files as the host user")
    check(final.get("spark1", {}).get("state") == "success",
          "the release, bound as a subdirectory of K2_RELEASES, is readable in the one-shot")
    out = subprocess.run(["docker", "run", "--rm", "--network", "none", "--pull", "never", "--mount",
                          f"type=volume,source={SPOOL},target=/s,readonly", IMG, "sh", "-c",
                          f"cat /s/{TAG}_fetch1/*/uid /s/{TAG}_fetch2/*/uid"], capture_output=True, text=True)
    check(out.stdout.split() == ["10001", "10001"],
          "fetch saw only its DAG's /spool/<dagId> and wrote <runId>/ there as its image user 10001",
          " ".join((out.stdout + out.stderr).split()))

    # A task marked failed through the API.
    run = trigger("kill")
    wait("the kill task's container", lambda: name("fetch", "kill") in alive(), 90)
    status, body = api("PATCH", f"dags/{TAG}_kill/dagRuns/{quote(run, safe='')}/taskInstances/kill",
                       {"new_state": "failed"})
    t0 = time.monotonic()
    try:
        wait("the container gone", lambda: name("fetch", "kill") not in alive(), 30)
        gone = time.monotonic() - t0
    except TimeoutError:
        gone = None
    check(status == 200 and gone is not None and gone <= 15,
          "a task marked failed through the API leaves no container within 15 s",
          f"PATCH {status}, gone after {gone if gone is None else round(gone, 1)} s")
    time.sleep(5)
    text = log("kill")
    killed = name("fetch", "kill")
    check(f"docker kill {killed}" in text, "on_kill fired under Airflow 3.3's supervisor and ran docker kill",
          "; ".join(m for m in ("shouldn't be running anymore", "docker kill", "exited 137") if m in text))

    # A crashed scheduler: the Airflow container killed under a running try, then started again.
    trigger("crash")
    first = name("fetch", "crash", 1)
    wait("try 1's container", lambda: first in alive(), 90)
    subprocess.run(["docker", "kill", AF], capture_output=True)
    orphan = first in alive()
    subprocess.run(["docker", "start", AF], capture_output=True)
    wait("the api-server again", healthy, 300)
    second, overlap = name("fetch", "crash", 2), False

    def retried():
        global overlap
        snap = alive()
        overlap = overlap or (first in snap and second in snap)
        st = states(["crash"]).get("crash", {})
        return st if st.get("state") in ("success", "failed") else None
    try:
        st = wait("the crash task's end", retried, 240)
    except TimeoutError:
        print(f"  crash: {states(['crash']).get('crash')}, alive {sorted(alive())}")
        raise
    check(orphan, "a killed scheduler leaves try 1's container running (the case the retry must clear)")
    check(st.get("state") == "success" and st.get("try_number") == 2 and not overlap and first not in alive(),
          "the retry's rm -f removed try 1's container before try 2 ran",
          f"state {st.get('state')}, try {st.get('try_number')}, overlap {overlap}")
    check(not alive(), "no one-shot container is left", " ".join(sorted(alive())))
except Exception as e:  # a timeout or an API refusal is a failed check, not a crash
    check(False, f"run stopped: {e}")
if fails:  # what the Airflow container was doing, before the trap removes it
    state = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}} exit {{.State.ExitCode}} oom {{.State.OOMKilled}}",
                            AF], capture_output=True, text=True)
    print(f"  {AF}: {(state.stdout + state.stderr).strip()}")
    logs = subprocess.run(["docker", "logs", "--tail", "40", AF], capture_output=True, text=True)
    print("\n".join("  | " + line for line in (logs.stdout + logs.stderr).splitlines()[-40:]))
print(f"{fails} FAIL")
sys.exit(1 if fails else 0)
PY
echo "oneshot-slots: wall $((SECONDS - START)) s"
exit $code
