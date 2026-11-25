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
