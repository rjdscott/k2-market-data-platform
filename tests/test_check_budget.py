"""
scripts/check-budget.py: the CI compose gate, fed fake `docker compose config`
services so no Docker is needed.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
budget = importlib.import_module("check-budget")

GIB = 2**30


def svc(cpus, gib, image="k2-thing:v1", restart="unless-stopped"):
    # memory as `config` prints it: a string of bytes
    limits = {"cpus": cpus, "memory": str(int(gib * GIB))}
    return {"image": image, "restart": restart, "deploy": {"resources": {"limits": limits}}}


def failures(default, governance=None, dockerfiles=(), legacy=None):
    return budget.check(default, governance or default, legacy or default, list(dockerfiles))[1]


def test_one_shots_count_towards_the_gated_bootstrap_peak():
    # 24 CPU steady plus a 2 CPU one-shot: steady is under 25, bootstrap is not.
    default = {"a": svc(24.0, 20), "init": svc(2.0, 1, restart="no")}
    report, fails = budget.check(default, default, default, [])
    assert "24.00 CPU" in report[0] and "26.00 CPU" in report[1]
    assert any("bootstrap default 26.00 CPU" in f and "ceiling" in f for f in fails)


def test_at_the_ceiling_passes_and_just_over_it_fails():
    assert failures({"a": svc(23.0, 42), "init": svc(2.0, 6, restart="no")}) == []
    assert any("ceiling" in f for f in failures({"a": svc(23.0, 42), "init": svc(2.01, 1, restart="no")}))


def test_memory_over_48_gib_fails():
    assert any("ceiling" in f for f in failures({"a": svc(4.0, 47), "init": svc(0.5, 1.001, restart="no")}))


def test_the_governance_profile_is_reported_not_gated():
    default = {"a": svc(10.0, 20)}
    governance = {**default, "catalog": svc(16.0, 20)}
    report, fails = budget.check(default, governance, default, [])
    assert fails == []
    assert "26.00 CPU" in report[2]


def test_the_legacy_profile_is_reported_not_gated_but_its_limits_and_pins_are():
    # The Prefect rollback: default plus two services, over the ceiling here on
    # purpose, so only its report line moves.
    default = {"a": svc(23.0, 30)}
    legacy = {**default, "prefect-server": svc(3.0, 8, image="prefecthq/prefect:3-python3.12")}
    report, fails = budget.check(default, default, legacy, [])
    assert fails == []
    assert report[3].startswith("bootstrap legacy") and "26.00 CPU" in report[3]
    # A service only the legacy profile starts is still held to limits and pins.
    bare = {**default, "prefect-worker": {"image": "k2-prefect-worker", "deploy": {}}}
    named = " ".join(failures(default, legacy=bare))
    assert "limits: prefect-worker" in named and "service prefect-worker" in named


def test_a_service_with_no_restart_key_is_reported_as_steady():
    default = {"a": svc(1.0, 1), "init": svc(0.5, 1, restart="no")}
    del default["a"]["restart"]
    report, _ = budget.check(default, default, default, [])
    assert "1 services" in report[0] and "1.00 CPU" in report[0]


def test_untagged_or_latest_image_fails_and_a_digest_passes():
    default = {
        "bare": svc(0.1, 0.1, image="redis"),
        "latest": svc(0.1, 0.1, image="redis:latest"),
        "port": svc(0.1, 0.1, image="localhost:5000/redis"),
        "digest": svc(0.1, 0.1, image="redis@sha256:" + "0" * 64),
        "tagged": svc(0.1, 0.1, image="docker.io/library/redis:7.4.1"),
    }
    named = {f.split()[1].rstrip(":") for f in failures(default)}
    assert named == {"bare", "latest", "port"}


def test_a_profiled_service_without_limits_fails():
    default = {"a": svc(1.0, 1)}
    governance = {**default, "catalog": {"image": "x:1", "deploy": {"resources": {"limits": {"cpus": 1}}}}}
    assert any("catalog" in f and "limits" in f for f in failures(default, governance))


def test_dockerfile_from_and_pip_pins():
    text = (
        "FROM rust:1-bookworm AS chef\n"
        "FROM chef AS builder\n"  # a stage name, not an image
        "FROM debian\n"
        "# RUN pip install commented-out\n"
        "RUN pip install --no-cache-dir \\\n"
        "    prometheus-client==0.21.1 \\\n"
        "    requests && uv pip install --system duckdb\n"
    )
    assert budget.dockerfile_problems("Dockerfile", text) == [
        "Dockerfile: FROM debian has no pinned tag",
        "Dockerfile: pip install requests has no == version",
        "Dockerfile: pip install duckdb has no == version",
    ]



def port(published, target=None, host_ip=None):
    # As `config` prints one: a range is already one entry per port.
    p = {"mode": "ingress", "target": target or int(published), "published": str(published), "protocol": "tcp"}
    return {**p, "host_ip": host_ip} if host_ip else p


def test_only_loopback_ports_pass():
    assert budget.exposure_problems({"minio": {"ports": [port(9000, host_ip="127.0.0.1")]}, "cadvisor": {}}) == []


def test_every_way_off_loopback_fails():
    services = {
        "bare": {"ports": [port(9000)]},                      # "9000:9000"
        "any4": {"ports": [port(9001, host_ip="0.0.0.0")]},   # "0.0.0.0:9001:9001"
        "any6": {"ports": [port(9002, host_ip="::")]},        # "[::]:9002:9002"
        "long": {"ports": [port(9003)]},                      # target/published, no host_ip
        "range": {"ports": [port(9004, host_ip="127.0.0.1"), port(9005)]},  # "9004-9005:9004-9005", one bare
        "lan": {"ports": [port(9006, host_ip="192.168.1.5")]},
    }
    named = [p.split()[1].rstrip(":") for p in budget.exposure_problems(services)]
    assert named == ["any4", "any6", "bare", "lan", "long", "range"]
    assert "port 9002:9002 is published on ::, not 127.0.0.1" in " ".join(budget.exposure_problems(services))


def test_host_networking_fails():
    assert budget.exposure_problems({"tool": {"network_mode": "host"}}) == [
        "service tool: network_mode host shares the host's interfaces"
    ]
    assert budget.exposure_problems({"side": {"network_mode": "service:minio"}}) == []


def test_main_gates_every_profile_not_only_the_three_it_sums(monkeypatch, capsys):
    # A service behind a profile check() never resolves still fails main().
    calls = []

    def fake(*profile):
        calls.append(profile)
        ok = {"a": {**svc(1.0, 1), "ports": [port(9000, host_ip="127.0.0.1")]}}
        return {**ok, "tools": {**svc(0.1, 0.1), "ports": [port(9009)]}} if profile == ("--profile", "*") else ok

    monkeypatch.setattr(budget, "compose_services", fake)
    assert budget.main() == 1
    assert ("--profile", "*") in calls
    assert "service tools: port 9009:9009 is published on every interface" in capsys.readouterr().out
