"""The catalog fence as docker-compose.yml declares it (ADR-059 part 7), checked without Docker.

Lakekeeper has no authentication: whatever reaches it can plant a view. So it publishes no port, it
is on k2-catalog alone, and k2-catalog holds exactly the services in
tests/access/catalog-fence-allow.txt (the list scripts/verify/catalog-fence.sh checks the running
stack against). Loopback binding of every published port is scripts/check-budget.py's check, not
repeated here. Each mutation below breaks the fence one way in the loaded document and must fail.

The two run one-shots are held the same way, as the Airflow one-shot operator runs them
(docker/airflow/dags/oneshot.py): fetch on k2-net alone with no storage key and no secret (A8);
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
    "minio": {"9000", "9001"},  # the S3 API and console: MinIO's own keys
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
    for n in ("lakekeeper", "lakekeeper-migrate"):
        s = services[n]
        if s.get("ports"):
            out.append(f"{n} publishes {s['ports']}: the catalog has no authentication, so no host port")
        if nets(s) != {"k2-catalog"} or s.get("network_mode"):
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
    if fetch.get("networks") != ["k2-net"]:
        out.append(f"fetch is on {fetch.get('networks')}: k2-net only (A8)")
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
HOLDS = {"lake-init": set(ENGINES), "trino": {"TRINO"}, "spark-iceberg": {"SPARK", "MAINTENANCE"}, "tools": {"TOOLS", "BACKUP"}}
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
    assert ENGINES == ["BACKUP", "MAINTENANCE", "SPARK", "TOOLS", "TRINO"]
    assert storage_problems(COMPOSE) == []
    for n in ("fetch", "dbt-runner", "k2-api", "prometheus", "grafana", "airflow-scheduler", "airflow-dag-processor",
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
    (env_set("tools", "K2_TOOLS_S3_SECRET_KEY", "${K2_BACKUP_S3_SECRET_KEY:?x}"), "required, never a default or another"),
    (lambda d: d["services"]["tools"]["environment"].pop("K2_BACKUP_S3_ACCESS_KEY"), "lacks its own pair"),
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
