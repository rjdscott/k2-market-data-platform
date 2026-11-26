"""
scripts/check-budget.py: the CI compose gate, fed fake `docker compose config`
services so no Docker is needed.
"""

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
budget = importlib.import_module("check-budget")

GIB = 2**30
SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"


def svc(cpus, gib, image="k2-thing:1.0.0", restart="unless-stopped"):
    # memory as `config` prints it: a string of bytes
    limits = {"cpus": cpus, "memory": str(int(gib * GIB))}
    return {"image": image, "restart": restart, "deploy": {"resources": {"limits": limits}}}


def check(default, governance=None, legacy=None, every=None, dockerfiles=()):
    governance, legacy = governance or default, legacy or default
    profiles = {"governance": governance, "legacy": legacy}
    return budget.check(default, profiles, every or {**default, **governance, **legacy}, list(dockerfiles))


def failures(default, governance=None, dockerfiles=(), legacy=None, every=None):
    return check(default, governance, legacy, every, dockerfiles)[1]


def test_one_shots_count_towards_the_gated_bootstrap_peak():
    # 24 CPU steady plus a 2 CPU one-shot: steady is under 25, bootstrap is not.
    default = {"a": svc(24.0, 20), "init": svc(2.0, 1, restart="no")}
    report, fails = check(default)
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
    report, fails = check(default, governance)
    assert fails == []
    assert "26.00 CPU" in report[2]


def test_the_legacy_profile_is_reported_not_gated_but_its_limits_and_pins_are():
    # The Prefect rollback: default plus two services, over the ceiling here on
    # purpose, so only its report line moves.
    default = {"a": svc(23.0, 30)}
    legacy = {**default, "prefect-server": svc(3.0, 8, image="prefecthq/prefect:3.9.5-python3.12")}
    report, fails = check(default, legacy=legacy)
    assert fails == []
    assert report[3].startswith("bootstrap legacy") and "26.00 CPU" in report[3]
    # A service only the legacy profile starts is still held to limits and pins.
    bare = {**default, "prefect-worker": {"image": "k2-prefect-worker", "deploy": {}}}
    named = " ".join(failures(default, legacy=bare))
    assert "limits: prefect-worker" in named and "service prefect-worker" in named


def test_a_service_with_no_restart_key_is_reported_as_steady():
    default = {"a": svc(1.0, 1), "init": svc(0.5, 1, restart="no")}
    del default["a"]["restart"]
    report, _ = check(default)
    assert "1 services" in report[0] and "1.00 CPU" in report[0]


@pytest.mark.parametrize("image", [
    "redis", "redis:latest", "localhost:5000/redis",
    # the floating tags the audit found passing: a bare major or a word
    "rust:1-bookworm", "postgres:15-alpine", "prefecthq/prefect:3-python3.12",
    "gcr.io/distroless/cc-debian12:nonroot", "python:3", "eclipse-temurin:21-jre-alpine",
    "x:stable", "x:v3",
])
def test_an_image_without_a_release_tag_is_unpinned(image):
    assert budget.unpinned(image)


@pytest.mark.parametrize("image", [
    "redis@sha256:" + "0" * 64, "gcr.io/distroless/cc-debian12:nonroot@sha256:" + "0" * 64,
    "docker.io/library/redis:7.4.1", "rust:1.100.0-bookworm", "clickhouse/clickhouse-server:24.3-alpine",
    "prom/prometheus:v3.2.0", "apache/airflow:slim-3.3.2-python3.12", "tabulario/spark-iceberg:3.5.5_1.8.1",
    "minio/minio:RELEASE.2025-09-07T16-13-09Z", "trinodb/trino:484", "localhost:5000/redis:7.4",
])
def test_a_release_tag_or_a_digest_is_pinned(image):
    assert not budget.unpinned(image)


def test_one_number_is_a_release_only_for_images_that_version_that_way():
    assert not budget.unpinned("trinodb/trino:484") and budget.unpinned("other/trino:484")


def test_compose_images_are_held_to_the_rule_except_ones_compose_builds():
    default = {
        "floating": svc(0.1, 0.1, image="postgres:15-alpine"),
        "ours": {**svc(0.1, 0.1, image="k2-capture:v3"), "build": {"context": "."}},
        "reuses-ours": svc(0.1, 0.1, image="k2-capture:v3"),   # built by `ours`, run again
        "theirs": svc(0.1, 0.1, image="other:v3"),
    }
    named = sorted(f.split()[1].rstrip(":") for f in failures(default))
    assert named == ["floating", "theirs"]


def test_replicas_and_scale_multiply_the_limits():
    # The audit's probe: 8 x 4 CPU / 8 GiB and 6 x 2 CPU / 4 GiB were summed as
    # 6 CPU / 12 GiB and passed; they are 44 CPU / 88 GiB. `config` writes
    # `scale: 6` into deploy.replicas too, so either key is read.
    a = svc(4.0, 8)
    a["deploy"]["replicas"] = 8
    b = {**svc(2.0, 4), "scale": 6}
    report, fails = check({"a": a, "b": b})
    assert "44.00 CPU" in report[1] and "88.000 GiB" in report[1]
    assert any("bootstrap default 44.00 CPU / 88.000 GiB is over" in f for f in fails)
    off = svc(30.0, 60)
    off["deploy"]["replicas"] = 0
    assert failures({"a": svc(1.0, 1), "off": off}) == []


def test_a_service_under_any_profile_is_held_to_limits_and_pins():
    # `tools` is neither governance nor legacy: only the `--profile '*'` resolve has it.
    default = {"a": svc(1.0, 1)}
    every = {**default, "tools": {"image": "busybox:latest", "deploy": {}}}
    named = " ".join(failures(default, every=every))
    assert "limits: tools" in named and "service tools: image 'busybox:latest'" in named


def test_a_profiled_service_without_limits_fails():
    default = {"a": svc(1.0, 1)}
    governance = {**default, "catalog": {"image": "x:1", "deploy": {"resources": {"limits": {"cpus": 1}}}}}
    assert any("catalog" in f and "limits" in f for f in failures(default, governance))


def test_dockerfile_from_copy_from_and_pip_pins():
    text = (
        "FROM rust:1-bookworm AS chef\n"
        "FROM chef AS builder\n"  # a stage name, not an image
        "FROM debian\n"
        "COPY --from=builder /a /a\n"
        "COPY --from=0 /b /b\n"
        "COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /uv\n"
        "COPY --from=ghcr.io/astral-sh/uv:latest /uv /uv\n"
        "# RUN pip install commented-out\n"
        "RUN pip install --no-cache-dir \\\n"
        "    prometheus-client==0.21.1 \\\n"
        "    requests && uv pip install --system duckdb\n"
    )
    assert budget.dockerfile_problems("Dockerfile", text) == [
        "Dockerfile: FROM rust:1-bookworm is not pinned",
        "Dockerfile: FROM debian is not pinned",
        "Dockerfile: COPY --from=ghcr.io/astral-sh/uv:latest is not pinned",
        "Dockerfile: pip install requests has no == version",
        "Dockerfile: pip install duckdb has no == version",
    ]


def test_docker_run_images_in_the_makefile_and_shell():
    text = (
        "test-rust:\n"
        "\tdocker run --rm -v \"$(CURDIR)\":/repo -w /repo -v cache:/usr/local/cargo \\\n"
        "\t  --user $(shell id -u):$(shell id -g) rust:1-bookworm sh -c 'cargo test'\n"
        "# docker run is how the tests start, a comment\n"
        "\tdocker run --rm --entrypoint sh --network=none prom/prometheus:v3.2.0 -c 'promtool'\n"
        "\tdocker run --rm $(IMAGE) true\n"
        "\tdocker run --rm k2-capture:v3 replay\n"
    )
    assert budget.run_images(text) == ["rust:1-bookworm", "prom/prometheus:v3.2.0", "k2-capture:v3"]
    assert budget.source_pin_problems("Makefile", text, frozenset({"k2-capture:v3"})) == [
        "Makefile: docker run rust:1-bookworm is not pinned"]
    assert budget.source_pin_problems("scripts/x.py", text) == []  # not shell: no docker run there


def test_every_uv_dependency_needs_an_exact_version():
    text = ('uv run --no-project --with pytest --with pyyaml==6.0.4 --with "confluent-kafka[avro]==2.15.0" \\\n'
            "  --with=ruff --with 'duckdb>=1' python x.py\n")
    named = [p.split()[3] for p in budget.source_pin_problems("scripts/x.sh", text)]
    assert named == ["pytest", "ruff", "'duckdb>=1'"]


def test_actions_are_pinned_by_full_sha_with_the_version_in_a_comment():
    text = (
        f"      - uses: actions/checkout@{SHA} # v7.0.1\n"
        f"      - uses: github/codeql-action/upload-sarif@{SHA}  # v4.39.5\n"
        "      - uses: ./.github/actions/local\n"
        "      - uses: docker://alpine:3.20.3\n"
        "      - uses: actions/checkout@v7\n"                    # first-party, still a moving tag
        "      - uses: Swatinem/rust-cache@v2\n"
        f"      - uses: astral-sh/setup-uv@{SHA}\n"              # no version comment
        f"      - uses: docker/setup-buildx-action@{SHA[:12]} # v4.4.1\n"
        "        uses: docker://alpine:latest\n"
    )
    lines = [int(p.split(":")[1]) for p in budget.source_pin_problems(".github/workflows/ci.yml", text)]
    assert lines == [5, 6, 7, 8, 9]
    assert budget.source_pin_problems("Makefile", "uses: actions/checkout@v7\n") == []



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
        tools = {"image": "busybox:latest", "deploy": {}, "ports": [port(9009)]}
        return {**ok, "tools": tools} if profile == ("--profile", "*") else ok

    monkeypatch.setattr(budget, "compose_services", fake)
    assert budget.main() == 1
    assert ("--profile", "*") in calls
    out = capsys.readouterr().out
    assert "service tools: port 9009:9009 is published on every interface" in out
    assert "limits: tools" in out and "service tools: image 'busybox:latest'" in out


def test_main_reports_the_default_plus_each_profile_a_service_names(monkeypatch, capsys):
    base = {"a": svc(1.0, 1)}
    extra = {"rollback": {**svc(2.0, 2), "profiles": ["legacy"]}, "lab": {**svc(4.0, 4), "profiles": ["research"]}}

    def fake(*profile):
        if profile == ("--profile", "*"):
            return {**base, **extra}
        name = profile[1] if profile else None
        return {**base, **{n: s for n, s in extra.items() if name in s["profiles"]}}

    monkeypatch.setattr(budget, "compose_services", fake)
    monkeypatch.setattr(budget, "tracked", lambda: [])
    assert budget.main() == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split("  ")[0] for line in out[:4]] == [
        "steady default", "bootstrap default", "bootstrap legacy", "bootstrap research"]
    assert "3.00 CPU" in out[2] and "5.00 CPU" in out[3]
