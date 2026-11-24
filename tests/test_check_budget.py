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


def failures(default, governance=None, dockerfiles=()):
    return budget.check(default, governance or default, list(dockerfiles))[1]


def test_one_shots_are_reported_but_not_gated():
    # 15 CPU steady plus a 2 CPU one-shot: bootstrap is over 16, steady is not.
    default = {"a": svc(15.0, 20), "init": svc(2.0, 1, restart="no")}
    report, fails = budget.check(default, default, [])
    assert fails == []
    assert "15.00 CPU" in report[0] and "17.00 CPU" in report[1]


def test_steady_over_the_cpu_ceiling_fails():
    assert any("ceiling" in f for f in failures({"a": svc(10.0, 4), "b": svc(6.5, 4)}))


def test_steady_over_40_gb_fails_even_under_40_gib():
    # 38 GiB is 40.8e9 bytes: under 40 GiB, over the 40 GB the budget states.
    assert any("ceiling" in f for f in failures({"a": svc(4.0, 38)}))


def test_a_service_with_no_restart_key_counts_as_steady():
    default = {"a": svc(17.0, 1)}
    del default["a"]["restart"]
    assert any("ceiling" in f for f in failures(default))


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
