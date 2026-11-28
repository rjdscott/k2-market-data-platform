"""The catalog fence as docker-compose.yml declares it (ADR-059 part 7), checked without Docker.

Lakekeeper has no authentication: whatever reaches it can plant a view. So it publishes no port, it
is on k2-catalog alone, and k2-catalog holds exactly the services in
tests/access/catalog-fence-allow.txt (the list scripts/verify/catalog-fence.sh checks the running
stack against). Loopback binding of every published port is scripts/check-budget.py's check, not
repeated here. MinIO publishes no port either (W1-23): the host reaches the lake's objects through
Trino and its rules alone. Each mutation below breaks the fence one way in the loaded document and
must fail.

The two run one-shots are held the same way, as the Airflow one-shot operator runs them
(docker/airflow/dags/oneshot.py): fetch on k2-egress alone, a plain bridge no other service joins,
with no storage key and no secret (A8);
dbt-runner with exactly the wrapper's seven environment names and its four files
(phase-001-interfaces.md, "The dbt wrapper"); both read-only, without capabilities or the socket.
"""

import copy
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())  # anchors and merge keys resolved
ALLOW = {ln.split("#")[0].strip() for ln in (ROOT / "tests/access/catalog-fence-allow.txt").read_text().splitlines()} - {""}
# The container ports a member may publish (on 127.0.0.1, check-budget.py), each behind its own login.
PUBLISHED = {
    "trino": {"8443"},  # the client endpoint: TLS and a password per principal
    "spark-iceberg": {"18080", "4040"},  # the history server and job UI: read-only pages, no catalog call
    "prefect-db": {"5432"},  # Postgres: its own passwords; the catalog's database among others
}


def nets(svc: dict) -> set[str]:
    return set(svc.get("networks") or [])  # a list or a mapping of network names


def targets(svc: dict) -> set[str]:
    """The container side of every published port, short or long syntax."""
    return {str(p["target"]) if isinstance(p, dict) else str(p).rsplit(":", 1)[-1].split("/")[0]
            for p in svc.get("ports") or []}


def problems(doc: dict) -> list[str]:
    services = doc["services"]
    direct = {n for n, s in services.items() if "k2-catalog" in nets(s)}
    reach = direct | {services[n].get("container_name") for n in direct}
    # network_mode service:<member> or container:<member> shares a member's interfaces: it is on k2-catalog too
    on = direct | {n for n, s in services.items() if str(s.get("network_mode", "")).partition(":")[2] in reach}
    out = []
    for n in ("lakekeeper", "lakekeeper-migrate", "minio"):
        s = services[n]
        if s.get("ports"):
            out.append(f"{n} publishes {s['ports']}: no host port, so no path to the catalog or the objects around Trino")
        if n != "minio" and (nets(s) != {"k2-catalog"} or s.get("network_mode")):
            out.append(f"{n} is on {sorted(nets(s))} {s.get('network_mode', '')}: k2-catalog only")
    if on != ALLOW:
        out.append(f"k2-catalog members differ from catalog-fence-allow.txt: unlisted {sorted(on - ALLOW)}, "
                   f"missing {sorted(ALLOW - on)}")
    for n in sorted(on):
        s = services[n]
        if extra := targets(s) - PUBLISHED.get(n, set()):
            out.append(f"{n} is on k2-catalog and publishes {sorted(extra)}")
        if any("docker.sock" in str(v) for v in s.get("volumes") or []):
            out.append(f"{n} is on k2-catalog with the Docker socket mounted")
        if set(s.get("profiles") or []) & {"fetch", "api"}:
            out.append(f"{n} is on k2-catalog in profile {s['profiles']}")
    net = doc["networks"]["k2-catalog"] or {}
    if net.get("name") or net.get("external"):
        out.append(f"network k2-catalog is renamed or external ({net}): catalog-fence.sh finds it as <project>_k2-catalog")
    return out


def test_the_compose_file_keeps_the_fence():
    assert problems(COMPOSE) == []


# The wrapper's environment, as the frozen run-time contract words it (k2gen's ONESHOT_KINDS is held
# to the same sentence by platform/k2gen/tests/test_emit_pipeline.py).
WRAPPER_ENV = set(re.search(r"Its environment is exactly `([A-Z0-9_ ]+)`", (ROOT / "docs/development/phase-001-interfaces.md")
                            .read_text().replace("\n  ", " "))[1].split())
WRAPPER_FILES = {"/run/k2-secrets/builder.password", "/run/k2-secrets/publisher.password",
                 "/run/k2-secrets/runs.password", "/run/k2-secrets/tls/ca.pem"}
VAR = re.compile(r"\$\{(?:[^${}]|\$\{[^}]*\})*\}")  # ${X}, or ${X:-<default>} whose default may hold ${Y} and a colon


def env_names(svc: dict) -> set[str]:
    env = svc.get("environment") or {}
    return set(env) if isinstance(env, dict) else {str(e).split("=", 1)[0] for e in env}


def secret_targets(svc: dict) -> set[str]:
    """The container side of every mount whose source is under ${K2_SECRETS}, short or long syntax."""
    return {v["target"] if isinstance(v, dict) else VAR.sub("", str(v)).split(":")[1]
            for v in svc.get("volumes") or [] if "k2-secrets" in str(v.get("source") if isinstance(v, dict) else v)}


def oneshot_problems(doc: dict) -> list[str]:
    services, out = doc["services"], []
    fetch, dbt = services["fetch"], services["dbt-runner"]
    if fetch.get("networks") != ["k2-egress"]:
        out.append(f"fetch is on {fetch.get('networks')}: k2-egress only (A8)")
    if others := sorted(n for n, s in services.items() if n != "fetch" and "k2-egress" in nets(s)):
        out.append(f"{others} on k2-egress: fetch's network alone, or a parser reaches them")
    if (net := doc["networks"].get("k2-egress", "absent")) != {"driver": "bridge"}:
        out.append(f"network k2-egress is {net}, not a plain bridge: renamed, external or internal breaks the operator or egress")
    if s3 := sorted(e for e in env_names(fetch) if "S3_" in e):
        out.append(f"fetch has storage key names {s3}")
    if any("k2-secrets" in str(v) for v in fetch.get("volumes") or []):
        out.append("fetch mounts a secret")
    if env_names(dbt) != WRAPPER_ENV:
        out.append(f"dbt-runner's environment is not the wrapper's: extra {sorted(env_names(dbt) - WRAPPER_ENV)}, "
                   f"missing {sorted(WRAPPER_ENV - env_names(dbt))}")
    if secret_targets(dbt) != WRAPPER_FILES:
        out.append(f"dbt-runner's secret files are {sorted(secret_targets(dbt))}, not {sorted(WRAPPER_FILES)}")
    for n, s in (("fetch", fetch), ("dbt-runner", dbt)):
        if any("docker.sock" in str(v) for v in s.get("volumes") or []):
            out.append(f"{n} mounts the Docker socket")
        if not (s.get("read_only") is True and s.get("cap_drop") == ["ALL"]
                and "no-new-privileges:true" in (s.get("security_opt") or [])):
            out.append(f"{n} is not read-only without capabilities and new privileges")
    return out


def test_the_run_one_shots_are_declared_as_the_operator_runs_them():
    assert len(WRAPPER_ENV) == 7 and oneshot_problems(COMPOSE) == []


def svc_set(svc, key, value):
    return lambda d: d["services"][svc].__setitem__(key, value)


@pytest.mark.parametrize("mutate,says", [
    (svc_set("fetch", "networks", ["k2-net", "k2-catalog"]), "fetch is on"),
    (svc_set("fetch", "networks", ["k2-net"]), "fetch is on"),
    (svc_set("fetch", "networks", ["k2-egress", "k2-net"]), "fetch is on"),
    (svc_set("minio", "networks", ["k2-net", "k2-catalog", "k2-egress"]), "['minio'] on k2-egress"),
    (svc_set("grafana", "networks", {"k2-net": None, "k2-egress": None}), "['grafana'] on k2-egress"),
    (lambda d: d["networks"].__setitem__("k2-egress", {"driver": "bridge", "internal": True}), "not a plain bridge"),
    (lambda d: d["networks"].__setitem__("k2-egress", {"driver": "bridge", "name": "shared"}), "not a plain bridge"),
    (lambda d: d["networks"].pop("k2-egress"), "not a plain bridge"),
    (svc_set("fetch", "environment", {"K2_RELEASE": None, "K2_SPARK_S3_ACCESS_KEY": "x"}), "storage key names"),
    (svc_set("fetch", "environment", ["K2_RELEASE", "K2_TRINO_S3_SECRET_KEY=x"]), "storage key names"),
    (lambda d: d["services"]["fetch"]["volumes"].append("${K2_SECRETS}/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro"),
     "fetch mounts a secret"),
    (lambda d: d["services"]["dbt-runner"]["environment"].__setitem__("AIRFLOW_HOME", "/x"), "extra ['AIRFLOW_HOME']"),
    (lambda d: d["services"]["dbt-runner"]["environment"].__setitem__("K2_TRINO_S3_ACCESS_KEY", "x"), "extra"),
    (lambda d: d["services"]["dbt-runner"]["environment"].pop("K2_PUB_USER"), "missing ['K2_PUB_USER']"),
    (lambda d: d["services"]["dbt-runner"]["volumes"].pop(), "secret files are"),
    (lambda d: d["services"]["dbt-runner"]["volumes"].append(
        "${K2_SECRETS:-${HOME}/k2-secrets}/principals/svc.platform.password:/run/k2-secrets/platform.password:ro"),
     "secret files are"),
    (lambda d: d["services"]["fetch"]["volumes"].append("/var/run/docker.sock:/var/run/docker.sock"),
     "fetch mounts the Docker socket"),
    (lambda d: d["services"]["dbt-runner"]["volumes"].append({"type": "bind", "source": "/var/run/docker.sock",
                                                              "target": "/var/run/docker.sock"}),
     "dbt-runner mounts the Docker socket"),
    (lambda d: d["services"]["fetch"].pop("read_only"), "fetch is not read-only"),
    (svc_set("dbt-runner", "cap_drop", ["NET_RAW"]), "dbt-runner is not read-only"),
])
def test_a_one_shot_off_its_contract_fails(mutate, says):
    doc = copy.deepcopy(COMPOSE)
    mutate(doc)
    found = oneshot_problems(doc)
    assert any(says in p for p in found), found


def ports(svc, *p):
    return lambda d: d["services"][svc].setdefault("ports", []).extend(p)


def join(svc):
    return lambda d: d["services"][svc].__setitem__("networks", [*nets(d["services"][svc]), "k2-catalog"])


@pytest.mark.parametrize("mutate,says", [
    (ports("lakekeeper", "127.0.0.1:8181:8181"), "lakekeeper publishes"),
    (ports("lakekeeper", "18181:8181"), "lakekeeper publishes"),
    (ports("lakekeeper", "127.0.0.1:1:1"), "lakekeeper publishes"),
    (ports("lakekeeper", "127.0.0.1:65535:65535/udp"), "lakekeeper publishes"),
    (ports("lakekeeper", {"target": 9999, "published": 9999, "host_ip": "127.0.0.1"}), "lakekeeper publishes"),
    (ports("lakekeeper-migrate", "127.0.0.1:5000:5000"), "lakekeeper-migrate publishes"),
    (ports("minio", "127.0.0.1:9000:9000"), "minio publishes"),
    (ports("minio", {"target": 9001, "published": 9001, "host_ip": "127.0.0.1"}), "minio publishes"),
    (ports("trino", "127.0.0.1:8080:8080"), "trino is on k2-catalog and publishes ['8080']"),
    (lambda d: d["services"]["lakekeeper"].__setitem__("networks", ["k2-catalog", "k2-net"]), "lakekeeper is on"),
    (lambda d: d["services"]["lakekeeper"].__setitem__("networks", {"k2-catalog": None, "k2-net": None}), "lakekeeper is on"),
    (join("grafana"), "unlisted ['grafana']"),
    (join("redpanda-console"), "unlisted ['redpanda-console']"),
    (join("fetch"), "unlisted ['fetch']"),
    (lambda d: d["services"]["grafana"].__setitem__("network_mode", "service:lakekeeper"), "unlisted ['grafana']"),
    (lambda d: d["services"]["grafana"].__setitem__("network_mode", "container:k2-lakekeeper"), "unlisted ['grafana']"),
    (lambda d: d["services"]["tools"]["volumes"].append("/var/run/docker.sock:/var/run/docker.sock"), "Docker socket"),
    (lambda d: d["services"]["dbt-runner"].__setitem__("profiles", ["api"]), "in profile ['api']"),
    (lambda d: d["services"]["lake-init"].__setitem__("networks", ["k2-net"]), "missing ['lake-init']"),
    (lambda d: d["networks"].__setitem__("k2-catalog", {"name": "shared"}), "renamed or external"),
])
def test_a_breach_of_the_fence_fails(mutate, says):
    doc = copy.deepcopy(COMPOSE)
    mutate(doc)
    found = problems(doc)
    assert any(says in p for p in found), found


# ---- storage keys (W1-3): every engine on its own MinIO user, the root pair in lake-init alone -------

ENGINES = sorted(p.stem.upper() for p in (ROOT / "docker/lake/minio/policies").glob("*.json"))  # init-lake.sh's list
# tools holds none: nothing it runs reads a storage key (scripts/backup-lake.sh passes BACKUP to its own
# `docker run -e`, and a step that needs one passes it by name for that run alone).
HOLDS = {"lake-init": set(ENGINES), "trino": {"TRINO"}, "spark-iceberg": {"SPARK", "MAINTENANCE"}}
ROOT_PAIR = {"minio", "lake-init"}  # MinIO itself; lake-init makes the users and the warehouses
# Spark's pair under the AWS SDK's names, in spark-iceberg alone: scripts/deploy.py's spark-sql DDL sets no key.
SDK = {"AWS_ACCESS_KEY_ID": "K2_SPARK_S3_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY": "K2_SPARK_S3_SECRET_KEY"}
REF = re.compile(r"\$\{([A-Za-z0-9_]+)")


def env_items(svc: dict) -> dict[str, str]:
    env = svc.get("environment") or {}
    if isinstance(env, dict):
        return {k: "" if v is None else str(v) for k, v in env.items()}
    return {str(e).split("=", 1)[0]: str(e).partition("=")[2] for e in env}


def storage_problems(doc: dict) -> list[str]:
    out = []
    for n, s in sorted(doc["services"].items()):
        env = env_items(s)
        own = {f"K2_{e}_S3_{h}_KEY" for e in HOLDS.get(n, ()) for h in ("ACCESS", "SECRET")}
        if n not in ROOT_PAIR and (root := sorted({k for k in env if k.startswith("MINIO_ROOT_")}
                                                  | {r for v in env.values() for r in REF.findall(v) if r.startswith("MINIO_ROOT_")})):
            out.append(f"{n} holds the root pair ({', '.join(root)})")
        named = {k for k in env if "S3_" in k}
        if extra := sorted(named - own - ({"K2_S3_ENDPOINT", "K2_S3_REGION"} if n == "spark-iceberg" else set())):
            out.append(f"{n} names storage keys not its own: {extra}")
        if missing := sorted(own - named):
            out.append(f"{n} lacks its own pair: {missing}")
        for k, v in env.items():
            refs = REF.findall(v)
            if k in own and not re.fullmatch(rf"\$\{{{k}:\?[^${{}}]*\}}", v):
                out.append(f"{n}: {k} is {v!r}, not ${{{k}:?<message>}}: required, never a default or another variable")
            elif k.startswith("AWS_") and (n != "spark-iceberg" or k not in {*SDK, "AWS_REGION"}
                                           or (k in SDK and refs != [SDK[k]])):
                out.append(f"{n}: {k}={v!r} is not Spark's own pair under the SDK's name")
            elif k not in own and k not in SDK and (other := [r for r in refs if re.fullmatch(r"K2_[A-Z]+_S3_[A-Z]+_KEY", r)]):
                out.append(f"{n}: {k} reads another engine's key {other}")
    return out


def test_every_engine_holds_its_own_storage_pair_and_only_lake_init_the_root():
    assert ENGINES == ["BACKUP", "MAINTENANCE", "RESTORE", "SPARK", "TOOLS", "TRINO"]
    assert storage_problems(COMPOSE) == []
    for n in ("fetch", "dbt-runner", "k2-api", "tools", "prometheus", "grafana", "airflow-scheduler", "airflow-dag-processor",
              "airflow-api-server", "airflow-migrate"):
        assert not [k for k in env_items(COMPOSE["services"][n]) if "S3_" in k or k.startswith(("AWS_", "MINIO_"))], n


def env_set(svc, key, value):
    return lambda d: d["services"][svc].setdefault("environment", {}).__setitem__(key, value)


@pytest.mark.parametrize("mutate,says", [
    (env_set("trino", "K2_TRINO_S3_ACCESS_KEY", "${MINIO_ROOT_USER}"), "trino holds the root pair"),
    (env_set("trino", "K2_TRINO_S3_SECRET_KEY", "${K2_TRINO_S3_SECRET_KEY:-${MINIO_ROOT_PASSWORD}}"), "trino holds the root pair"),
    (env_set("trino", "K2_TRINO_S3_SECRET_KEY", "${K2_TRINO_S3_SECRET_KEY:-change-me}"), "required, never a default"),
    (env_set("spark-iceberg", "MINIO_ROOT_USER", "${MINIO_ROOT_USER}"), "spark-iceberg holds the root pair"),
    (env_set("spark-iceberg", "K2_TRINO_S3_ACCESS_KEY", "${K2_TRINO_S3_ACCESS_KEY:?x}"), "not its own"),
    (env_set("spark-iceberg", "AWS_SECRET_ACCESS_KEY", "${K2_MAINTENANCE_S3_SECRET_KEY:?x}"), "not Spark's own pair"),
    (env_set("tools", "K2_SPARK_S3_SECRET_KEY", "${K2_SPARK_S3_SECRET_KEY:?x}"), "not its own"),
    (env_set("tools", "K2_TOOLS_S3_SECRET_KEY", "${K2_TOOLS_S3_SECRET_KEY:?x}"), "tools names storage keys not its own"),
    (env_set("tools", "K2_BACKUP_S3_ACCESS_KEY", "${K2_BACKUP_S3_ACCESS_KEY:?x}"), "tools names storage keys not its own"),
    (env_set("spark-iceberg", "K2_SPARK_S3_SECRET_KEY", "${K2_MAINTENANCE_S3_SECRET_KEY:?x}"), "required, never a default or another"),
    (lambda d: d["services"]["trino"]["environment"].pop("K2_TRINO_S3_ACCESS_KEY"), "lacks its own pair"),
    (env_set("airflow-scheduler", "K2_MAINTENANCE_S3_ACCESS_KEY", "${K2_MAINTENANCE_S3_ACCESS_KEY:?x}"), "not its own"),
    (env_set("airflow-scheduler", "AWS_ACCESS_KEY_ID", "${K2_SPARK_S3_ACCESS_KEY:?x}"), "not Spark's own pair"),
    (env_set("prometheus", "PASS", "${K2_TOOLS_S3_SECRET_KEY}"), "reads another engine's key"),
    (lambda d: d["services"]["dbt-runner"]["environment"].__setitem__("MINIO_ROOT_PASSWORD", "x"), "dbt-runner holds the root pair"),
])
def test_a_storage_key_out_of_place_fails(mutate, says):
    doc = copy.deepcopy(COMPOSE)
    mutate(doc)
    found = storage_problems(doc)
    assert any(says in p for p in found), found


# ---- the run path (W1-5, W1-20): code read through the pointers, the scheduler wired for the factory --

AIRFLOW = ("airflow-migrate", "airflow-api-server", "airflow-scheduler", "airflow-dag-processor")
DAGS_FOLDER = "/k2/releases/pointers/airflow/platform/docker/airflow/dags"  # factory.py's POINTER + the release's platform/
RULES = "/k2/releases/pointers/prometheus/platform/docker/prometheus/rules/*.yml"
COMPILED_RULES = "/k2/releases/pointers/prometheus/compiled/prometheus/*.yml"  # k2gen emit/alerts.py (I-5)
# Where a release before phase 002 mounts the checkout's rules (its compose); prometheus.yml is the
# checkout's under every release, so after a rollback to one this glob is the only one that matches.
OLD_RULES = "/etc/prometheus/rules/*.yml"
POOLS = {"lake_spark": "1", "fetch": "1", "dbt": "2"}  # factory.py POOLS, slots as W0-8 proved them
# oneshot.py's host-side names, and dbt-runner's fixed values it passes on by name (record reads three of them).
ONESHOT_ENV = {"K2_RELEASES", "K2_SECRETS", "K2_COMPOSE_PROJECT", "K2_RUN_UID", "K2_RUN_GID"}
PASSED_ON = {"K2_TRINO_HOST", "K2_TRINO_INTERNAL_PORT", "K2_RUNS_USER", "K2_CATALOG_URI"}
PROMETHEUS = yaml.safe_load((ROOT / "docker/prometheus/prometheus.yml").read_text())
OWNER = re.compile(r"chown (\d+:\d+) /spool\b")


def run_path_problems(doc: dict, prometheus: dict, spark_dockerfile: str, fetch_dockerfile: str) -> list[str]:
    services, out = doc["services"], []
    for n in AIRFLOW:
        s = services[n]
        if any("docker/airflow/dags" in str(v.get("source") if isinstance(v, dict) else v) for v in s.get("volumes") or []):
            out.append(f"{n} bind-mounts the checkout's docker/airflow/dags")
        if (got := env_items(s).get("AIRFLOW__CORE__DAGS_FOLDER")) != DAGS_FOLDER:
            out.append(f"{n}'s DAG folder is {got!r}, not the airflow pointer's {DAGS_FOLDER}")
        if n != "airflow-scheduler" and (extra := sorted(env_names(s) & (ONESHOT_ENV | PASSED_ON))):
            out.append(f"{n} has the one-shot environment {extra}: the scheduler's alone")
    sched, dbt = env_items(services["airflow-scheduler"]), env_items(services["dbt-runner"])
    if missing := sorted((ONESHOT_ENV | PASSED_ON) - set(sched)):
        out.append(f"airflow-scheduler lacks {missing}")
    if wrong := sorted(k for k in PASSED_ON if k in sched and sched[k] != dbt.get(k)):
        out.append(f"airflow-scheduler passes on {wrong} with values other than dbt-runner's")
    if any(not re.fullmatch(rf"\$\{{{k}:\?[^${{}}]*\}}", sched.get(k, "")) for k in ("K2_RUN_UID", "K2_RUN_GID")):
        out.append("airflow-scheduler's K2_RUN_UID and K2_RUN_GID are not required (${NAME:?...})")
    if not re.fullmatch(r"\$\{K2_RUN_UID:\?[^${}]*\}:0", str(services["airflow-scheduler"].get("user"))):
        out.append(f"airflow-scheduler runs as {services['airflow-scheduler'].get('user')!r}, not K2_RUN_UID with group 0")
    command = " ".join(services["airflow-migrate"].get("command") or [])
    if bad := [p for p, n in POOLS.items() if not re.search(rf"&& airflow pools set {p} {n} ", command)]:
        out.append(f"airflow-migrate does not set the pools {bad} after the migration")
    spool = [v for v in services["spark-iceberg"].get("volumes") or [] if str(v).split(":")[0] == "k2-spool"]
    if spool != ["k2-spool:/spool:ro"]:
        out.append(f"spark-iceberg mounts the spool as {spool}, not k2-spool:/spool:ro")
    if OWNER.findall(spark_dockerfile) != OWNER.findall(fetch_dockerfile) or len(OWNER.findall(fetch_dockerfile)) != 1:
        out.append(f"/spool's owner in the Spark image {OWNER.findall(spark_dockerfile)} is not fetch's "
                   f"{OWNER.findall(fetch_dockerfile)}: the first to mount the empty volume decides its owner")
    if prometheus.get("rule_files") != [RULES, COMPILED_RULES, OLD_RULES]:
        out.append(f"prometheus.yml reads rules from {prometheus.get('rule_files')}, not the pointer's {RULES} "
                   f"and {COMPILED_RULES}, then a pre-phase-002 release's {OLD_RULES}")
    # Nothing at the old path under this compose, so the two globs never both match: no group loads twice.
    targets = [v.get("target", "") if isinstance(v, dict) else str(v).split(":")[1] for v in services["prometheus"].get("volumes") or []]
    if any("docker/prometheus/rules" in str(v) for v in services["prometheus"].get("volumes") or []) \
            or any(t.rstrip("/") in ("/etc/prometheus", "/etc/prometheus/rules") or t.startswith("/etc/prometheus/rules/") for t in targets):
        out.append("prometheus mounts the checkout's rules")
    return out


DOCKERFILES = tuple((ROOT / f).read_text() for f in ("docker/spark/Dockerfile", "docker/fetch/Dockerfile"))


def test_the_run_code_is_read_through_the_pointers_and_the_scheduler_is_wired():
    assert run_path_problems(COMPOSE, PROMETHEUS, *DOCKERFILES) == []


@pytest.mark.parametrize("mutate,says", [
    (lambda d: d["services"]["airflow-dag-processor"]["volumes"].append("./docker/airflow/dags:/opt/airflow/dags:ro"),
     "airflow-dag-processor bind-mounts"),
    (lambda d: d["services"]["airflow-scheduler"]["volumes"].append(
        {"type": "bind", "source": "./docker/airflow/dags", "target": "/opt/airflow/dags"}), "airflow-scheduler bind-mounts"),
    (env_set("airflow-scheduler", "AIRFLOW__CORE__DAGS_FOLDER", "/opt/airflow/dags"), "DAG folder is '/opt/airflow/dags'"),
    (lambda d: d["services"]["airflow-dag-processor"].__setitem__("environment", {}), "airflow-dag-processor's DAG folder"),
    (env_set("airflow-api-server", "K2_SECRETS", "/x"), "airflow-api-server has the one-shot environment"),
    (env_set("airflow-dag-processor", "K2_RUNS_USER", "svc.runs"), "airflow-dag-processor has the one-shot"),
    (lambda d: d["services"]["airflow-scheduler"]["environment"].pop("K2_COMPOSE_PROJECT"), "lacks ['K2_COMPOSE_PROJECT']"),
    (env_set("airflow-scheduler", "K2_TRINO_HOST", "localhost"), "passes on ['K2_TRINO_HOST']"),
    (env_set("airflow-scheduler", "K2_RUN_UID", "${K2_RUN_UID:-1000}"), "not required"),
    (svc_set("airflow-scheduler", "user", "${K2_RUN_UID:?x}:${K2_RUN_GID:?x}"), "not K2_RUN_UID with group 0"),
    (lambda d: d["services"]["airflow-scheduler"].pop("user"), "runs as None"),
    (svc_set("airflow-migrate", "command", ["bash", "/opt/k2/init-db.sh"]), "does not set the pools"),
    (svc_set("airflow-migrate", "command", ["bash", "-c", "bash /opt/k2/init-db.sh && airflow pools set lake_spark 1 x"
                                            " && airflow pools set fetch 1 x && airflow pools set dbt 4 x"]), "['dbt']"),
    (lambda d: d["services"]["spark-iceberg"]["volumes"].remove("k2-spool:/spool:ro"), "mounts the spool as []"),
    (lambda d: d["services"]["spark-iceberg"]["volumes"].__setitem__(-1, "k2-spool:/spool"), "not k2-spool:/spool:ro"),
    (lambda d: d["services"]["prometheus"]["volumes"].append("./docker/prometheus/rules:/etc/prometheus/rules:ro"),
     "prometheus mounts the checkout's rules"),
    (lambda d: d["services"]["prometheus"]["volumes"].append({"type": "bind", "source": "./rules", "target": "/etc/prometheus/rules"}),
     "prometheus mounts the checkout's rules"),
    (lambda d: d["services"]["prometheus"]["volumes"].append("./x.yml:/etc/prometheus/rules/x.yml:ro"),
     "prometheus mounts the checkout's rules"),
])
def test_a_run_path_off_the_pointers_fails(mutate, says):
    doc = copy.deepcopy(COMPOSE)
    mutate(doc)
    found = run_path_problems(doc, PROMETHEUS, *DOCKERFILES)
    assert any(says in p for p in found), found


@pytest.mark.parametrize("rules", [["rules/*.yml"], [RULES], [OLD_RULES], [OLD_RULES, RULES], [RULES, "rules/*.yml"],
                                   [RULES, OLD_RULES], [COMPILED_RULES, RULES, OLD_RULES], None])
def test_prometheus_rules_off_the_pointer_fail(rules):
    found = run_path_problems(COMPOSE, {**PROMETHEUS, "rule_files": rules}, *DOCKERFILES)
    assert any("not the pointer's" in p for p in found), found


@pytest.mark.parametrize("spark", ["", "RUN mkdir /spool && chown 185:185 /spool", "RUN mkdir /spool"])
def test_a_spark_image_whose_spool_is_not_fetchs_fails(spark):
    assert any("is not fetch's" in p for p in run_path_problems(COMPOSE, PROMETHEUS, spark, DOCKERFILES[1]))


# ---- k2-api (W3-5): Trino only, as svc.api; never the catalog, a storage key or the socket ------------

API_ENV = {"K2_TRINO_HOST", "K2_TRINO_INTERNAL_PORT"}  # services/k2-api/k2api/db.py reads these two alone
API_MOUNTS = {"/k2/releases": "${K2_RELEASES:-${HOME}/k2-releases}",
              "/run/k2-secrets/api": "${K2_SECRETS:-${HOME}/k2-secrets}/api",
              "/run/k2-secrets/api.password": "${K2_SECRETS:-${HOME}/k2-secrets}/principals/svc.api.password",
              "/run/k2-secrets/tls/ca.pem": "${K2_SECRETS:-${HOME}/k2-secrets}/tls/ca.pem"}
SITE_RO = {"type": "volume", "source": "k2-site", "target": "/srv/k2-site", "read_only": True}  # /catalog/, /dbt-docs/


def api_problems(doc: dict) -> list[str]:
    s, out = doc["services"]["k2-api"], []
    if s.get("profiles"):
        out.append(f"k2-api is in profile {s['profiles']}: deploy moves only the default set, and plan 003 has it report the release")
    if s.get("networks") != ["k2-net"] or s.get("network_mode"):
        out.append(f"k2-api is on {s.get('networks')} {s.get('network_mode', '')}: k2-net only, never k2-catalog")
    if env_names(s) != API_ENV:
        out.append(f"k2-api's environment is {sorted(env_names(s))}, not {sorted(API_ENV)}")
    mounts = {}
    volumes = [v for v in s.get("volumes") or [] if v != SITE_RO]
    if len(volumes) != len(s.get("volumes") or []) - 1:
        out.append(f"k2-api does not mount the site volume once, read-only, as {SITE_RO}")
    for v in volumes:
        if not (isinstance(v, dict) and v.get("type") == "bind" and v.get("read_only") is True
                and (v.get("bind") or {}).get("create_host_path") is False):
            out.append(f"k2-api mounts {v} other than a long-syntax, read-only bind never created when missing")
        else:
            mounts[v["target"]] = v["source"]
    if mounts != API_MOUNTS:
        out.append(f"k2-api's mounts are {mounts}, not {API_MOUNTS}")
    if any("docker.sock" in str(v) for v in s.get("volumes") or []):
        out.append("k2-api mounts the Docker socket")
    if s.get("ports") != ["127.0.0.1:${K2_API_PORT:-8095}:8095"]:
        out.append(f"k2-api publishes {s.get('ports')}, not its one port on loopback")
    if not re.fullmatch(r"\$\{K2_RUN_UID:\?[^${}]*\}:0", str(s.get("user"))):
        out.append(f"k2-api runs as {s.get('user')!r}, not K2_RUN_UID with group 0")
    if not (s.get("read_only") is True and s.get("cap_drop") == ["ALL"]
            and "no-new-privileges:true" in (s.get("security_opt") or []) and s.get("tmpfs")):
        out.append("k2-api is not read-only with a tmpfs, without capabilities and new privileges")
    if s.get("image") != "k2-api:local" or (s.get("build") or {}).get("dockerfile") != "services/k2-api/Dockerfile":
        out.append(f"k2-api is not the image built from services/k2-api/Dockerfile: {s.get('image')}")
    return out


def test_k2_api_reaches_trino_alone_with_exactly_its_names_and_mounts():
    assert "k2-api" not in ALLOW and api_problems(COMPOSE) == []


def api_vol(target, **change):
    def mutate(d):
        for v in d["services"]["k2-api"]["volumes"]:
            if v["target"] == target:
                v.update(change)
                for k in [k for k, x in change.items() if x is None]:
                    v.pop(k)
    return mutate


@pytest.mark.parametrize("mutate,says", [
    (svc_set("k2-api", "networks", ["k2-net", "k2-catalog"]), "k2-net only"),
    (svc_set("k2-api", "networks", ["k2-catalog"]), "k2-net only"),
    (svc_set("k2-api", "network_mode", "service:trino"), "k2-net only"),
    (lambda d: d["services"]["k2-api"]["environment"].__setitem__("K2_TRINO_USER", "svc.platform"), "environment is"),
    (lambda d: d["services"]["k2-api"]["environment"].__setitem__("K2_TRINO_S3_ACCESS_KEY", "x"), "environment is"),
    (lambda d: d["services"]["k2-api"]["environment"].pop("K2_TRINO_HOST"), "environment is"),
    (lambda d: d["services"]["k2-api"]["volumes"].append("/var/run/docker.sock:/var/run/docker.sock"), "Docker socket"),
    (lambda d: d["services"]["k2-api"]["volumes"].append({
        "type": "bind", "source": "${K2_SECRETS:-${HOME}/k2-secrets}/principals/svc.platform.password",
        "target": "/run/k2-secrets/platform.password", "read_only": True, "bind": {"create_host_path": False}}), "mounts are"),
    (lambda d: d["services"]["k2-api"]["volumes"].pop(1), "mounts are"),
    (api_vol("/run/k2-secrets/api", source="${K2_SECRETS:-${HOME}/k2-secrets}"), "mounts are"),
    (api_vol("/run/k2-secrets/api.password", read_only=False), "other than a long-syntax, read-only"),
    (api_vol("/run/k2-secrets/tls/ca.pem", bind=None), "other than a long-syntax, read-only"),
    (api_vol("/k2/releases", source="${K2_RELEASES:-${HOME}/k2-releases}/pointers/k2-api"), "mounts are"),
    (svc_set("k2-api", "ports", ["8095:8095"]), "not its one port on loopback"),
    (svc_set("k2-api", "ports", ["127.0.0.1:8095:8095", "127.0.0.1:9095:9095"]), "not its one port on loopback"),
    (svc_set("k2-api", "user", "0:0"), "not K2_RUN_UID"),
    (lambda d: d["services"]["k2-api"].pop("read_only"), "not read-only"),
    (svc_set("k2-api", "cap_drop", ["NET_RAW"]), "not read-only"),
    (svc_set("k2-api", "security_opt", []), "not read-only"),
    (svc_set("k2-api", "image", "alpine:3.20"), "not the image built"),
    (svc_set("k2-api", "profiles", ["api"]), "in profile ['api']"),
    (api_vol("/srv/k2-site", read_only=False), "site volume once, read-only"),
    (lambda d: d["services"]["k2-api"]["volumes"].remove(SITE_RO), "site volume once, read-only"),
    (lambda d: d["services"]["k2-api"]["volumes"].append("k2-site:/srv/other"), "other than a long-syntax"),
])
def test_k2_api_off_its_contract_fails(mutate, says):
    doc = copy.deepcopy(COMPOSE)
    mutate(doc)
    found = api_problems(doc)
    assert any(says in p for p in found), found


def test_k2_api_on_the_catalog_network_breaks_the_fence():
    doc = copy.deepcopy(COMPOSE)
    join("k2-api")(doc)
    assert any("unlisted ['k2-api']" in p for p in problems(doc))


# ---- the k2-site volume (W3-11): written only by the generators' one-shots, read-only in k2-api -----

SITE_WRITERS = {"tools": "/site"}  # k2site.py; `k2-dbt docs` mounts it by deploy's docker run, not compose
MODE = re.compile(r"mkdir -m (\d+) (/site|/srv/k2-site)\b")


def site_problems(doc: dict, dockerfiles: dict[str, str]) -> list[str]:
    out = []
    for n, s in sorted(doc["services"].items()):
        for v in s.get("volumes") or []:
            long = v if isinstance(v, dict) else {"source": str(v).split(":")[0], "target": str(v).split(":")[1],
                                                  "read_only": str(v).endswith(":ro")}
            if long.get("source") != "k2-site":
                continue
            want = (SITE_WRITERS[n], False) if n in SITE_WRITERS else ("/srv/k2-site", True) if n == "k2-api" else None
            if (long.get("target"), bool(long.get("read_only"))) != want:
                out.append(f"{n} mounts k2-site at {long.get('target')} read_only={long.get('read_only')}, not {want}")
    if "k2-site" not in (doc.get("volumes") or {}):
        out.append("no volume k2-site")
    modes = {path: MODE.findall(text) for path, text in dockerfiles.items()}
    if sorted(m for found in modes.values() for m, _ in found) != ["1777"] * len(dockerfiles):
        out.append(f"the site's mount points are not each made once, mode 1777, in each image that mounts it: {modes}"
                   " (the first image to mount the empty volume sets its root)")
    return out


SITE_DOCKERFILES = {f: (ROOT / f).read_text() for f in ("docker/tools/Dockerfile", "services/k2-api/Dockerfile", "docker/dbt-runner/Dockerfile")}


def test_the_site_volume_is_written_by_the_generators_and_read_only_in_k2_api():
    assert site_problems(COMPOSE, SITE_DOCKERFILES) == []


@pytest.mark.parametrize("mutate,says", [
    (lambda d: d["services"]["trino"]["volumes"].append("k2-site:/srv/k2-site:ro"), "trino mounts k2-site"),
    (lambda d: d["services"]["dbt-runner"]["volumes"].append({"type": "volume", "source": "k2-site", "target": "/site"}),
     "dbt-runner mounts k2-site"),
    (lambda d: [v.pop("read_only", None) for v in d["services"]["k2-api"]["volumes"] if v.get("source") == "k2-site"],
     "k2-api mounts k2-site"),
    (lambda d: [v.__setitem__("read_only", True) for v in d["services"]["tools"]["volumes"] if v.get("source") == "k2-site"],
     "tools mounts k2-site"),
    (lambda d: d["volumes"].pop("k2-site"), "no volume k2-site"),
])
def test_a_site_volume_off_its_owners_fails(mutate, says):
    doc = copy.deepcopy(COMPOSE)
    mutate(doc)
    found = site_problems(doc, SITE_DOCKERFILES)
    assert any(says in p for p in found), found


@pytest.mark.parametrize("tools", ["RUN mkdir /site", "RUN mkdir -m 0755 /site", ""])
def test_an_image_whose_site_mount_point_is_not_1777_fails(tools):
    assert any("mode 1777" in p for p in site_problems(COMPOSE, {**SITE_DOCKERFILES, "docker/tools/Dockerfile": tools}))
