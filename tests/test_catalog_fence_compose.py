"""The catalog fence as docker-compose.yml declares it (ADR-059 part 7), checked without Docker.

Lakekeeper has no authentication: whatever reaches it can plant a view. So it publishes no port, it
is on k2-catalog alone, and k2-catalog holds exactly the services in
tests/access/catalog-fence-allow.txt (the list scripts/verify/catalog-fence.sh checks the running
stack against). Loopback binding of every published port is scripts/check-budget.py's check, not
repeated here. Each mutation below breaks the fence one way in the loaded document and must fail.
"""

import copy
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
