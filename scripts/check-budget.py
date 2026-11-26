#!/usr/bin/env python3
"""The CI `compose` job's budget and pinning gate, runnable locally:

    python3 scripts/check-budget.py

Resolves the compose file plain, with `--profile governance`, with
`--profile legacy` and with `--profile '*'` (every service of every profile),
prints four limit sums and exits 1 on any of:

- the bootstrap default (every default-profile service, one-shots included,
  because they run beside the rest at `docker compose up`; each service's
  limits times its `deploy.replicas`, which compose also sets from `scale`) above 25 CPU /
  48 GiB, the ceiling ADR-045 sets in place of ADR-034's 20 CPU / 36 GiB
  (which replaced ADR-010's 16 CPU / 40 GB). The steady default and the two
  profiled bootstraps are reported only: no service has the governance profile
  today, so its line equals the default, and legacy is the Prefect rollback,
  run only in place of Airflow's writes.
- a service under any profile that does not declare both a CPU and a memory
  limit, so a service behind a profile cannot ship without one.
- a compose image, in any profile, or a Dockerfile FROM, with no tag or the tag `latest`.
- a `pip install` package in a Dockerfile with no `==` version.
- a published port not bound to 127.0.0.1, or `network_mode: host`, in any
  service of any profile: a bare
  `9000:9000` listens on every interface, the local network included, and host
  networking publishes whatever the container listens on (SECURITY.md).

A one-shot is a service with `restart: "no"`, the rule
docs/operations/docker-resources.md already uses; it only decides which
reported line a service lands on, since the gated sum counts both.
Stdlib only: `docker compose config --format json` needs no YAML parser.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GIB = 2**30
CPU_CEILING = 25.0
# GiB, the unit compose limits are in (docs/operations/docker-resources.md).
MEM_CEILING = 48 * GIB


def compose_services(*profile: str) -> dict:
    out = subprocess.run(
        ["docker", "compose", "--env-file", ".env.example", *profile, "config", "--format", "json"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)["services"]


def limits(svc: dict) -> dict:
    return ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}


def replicas(svc: dict) -> int:
    """Containers compose starts for a service: `config` writes `scale: N` into
    deploy.replicas too; 0 is a service that starts none."""
    n = (svc.get("deploy") or {}).get("replicas", svc.get("scale"))
    return 1 if n is None else int(n)


def total(services: dict) -> tuple[float, int]:
    """CPU and memory bytes summed over limits, times replicas. `config`
    normalises memory to bytes."""
    cpu = sum(float(limits(s).get("cpus", 0)) * replicas(s) for s in services.values())
    mem = sum(int(limits(s).get("memory", 0)) * replicas(s) for s in services.values())
    return cpu, mem


def unpinned(image: str) -> bool:
    """No tag, or `latest`. A digest is a pin."""
    if "@sha256:" in image:
        return False
    name = image.rsplit("/", 1)[-1]  # a registry port (host:5000/x) is not a tag
    return ":" not in name or name.endswith(":latest")


def dockerfile_problems(path: str, text: str) -> list[str]:
    problems, stages = [], {"scratch"}
    for line in text.replace("\\\n", " ").splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        if words[0].upper() == "FROM":
            args = [w for w in words[1:] if not w.startswith("--")]
            if args[0] not in stages and unpinned(args[0]):
                problems.append(f"{path}: FROM {args[0]} has no pinned tag")
            if len(args) == 3 and args[1].upper() == "AS":
                stages.add(args[2])
        # ponytail: pip only. apt-get, cargo and the jar downloads are not
        # checked; cargo-chef carries --version and the jars a sha256 already.
        for m in re.finditer(r"pip3? install\s+([^&;|]*)", line):
            for pkg in m.group(1).split():
                if not pkg.startswith("-") and "==" not in pkg:
                    problems.append(f"{path}: pip install {pkg} has no == version")
    return problems


def check(
    default: dict, governance: dict, legacy: dict, every: dict, dockerfiles: list[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """(report lines, failures). Pure, so a test can hand it dicts. `every` is
    the `--profile '*'` resolve: limits and pins are held there, so a service
    behind a profile nobody names here is still checked."""
    steady = {n: s for n, s in default.items() if s.get("restart") != "no"}
    report = []
    for label, services in (
        ("steady default", steady),
        ("bootstrap default", default),
        ("bootstrap governance", governance),
        ("bootstrap legacy", legacy),
    ):
        cpu, mem = total(services)
        report.append(f"{label:<21} {len(services):>3} services  {cpu:6.2f} CPU  {mem / GIB:7.3f} GiB")

    failures = []
    cpu, mem = total(default)
    if cpu > CPU_CEILING or mem > MEM_CEILING:
        failures.append(
            f"bootstrap default {cpu:.2f} CPU / {mem / GIB:.3f} GiB is over the "
            f"{CPU_CEILING:g} CPU / {MEM_CEILING / GIB:g} GiB ceiling"
        )
    no_limits = sorted(n for n, s in every.items() if not {"cpus", "memory"} <= limits(s).keys())
    if no_limits:
        failures.append("no cpus + memory in deploy.resources.limits: " + ", ".join(no_limits))
    for name, svc in sorted(every.items()):
        if unpinned(svc.get("image", "")):
            failures.append(f"service {name}: image {svc.get('image') or '(none)'!r} has no pinned tag")
    for path, text in dockerfiles:
        failures += dockerfile_problems(path, text)
    return report, failures


def exposure_problems(services: dict) -> list[str]:
    """Ports off loopback, and host networking. `config` normalises every port
    syntax (short, long, a range, one entry per port) to host_ip + published +
    target, with no host_ip for a bare "9000:9000"."""
    problems = []
    for name, svc in sorted(services.items()):
        if svc.get("network_mode") == "host":
            problems.append(f"service {name}: network_mode host shares the host's interfaces")
        for port in svc.get("ports") or []:
            if port.get("host_ip") != "127.0.0.1":
                problems.append(
                    f"service {name}: port {port.get('published')}:{port.get('target')} is published on "
                    f"{port.get('host_ip') or 'every interface'}, not 127.0.0.1"
                )
    return problems


def main() -> int:
    # ponytail: one level deep is where every Dockerfile lives today
    # (docker/*/, services/*/, scripts/screenshots/); rglob when one moves deeper.
    dockerfiles = [
        (str(p.relative_to(ROOT)), p.read_text())
        for d in ("docker", "services", "scripts")
        for p in sorted((ROOT / d).glob("*/Dockerfile*"))
        if p.suffix != ".dockerignore"
    ]
    every = compose_services("--profile", "*")
    report, failures = check(
        compose_services(),
        compose_services("--profile", "governance"),
        compose_services("--profile", "legacy"),
        every,
        dockerfiles,
    )
    failures += exposure_problems(every)
    print("\n".join(report))
    for f in failures:
        print(f"FAIL: {f}")
    if not failures:
        print(f"PASS: bootstrap default within {CPU_CEILING:g} CPU / {MEM_CEILING / GIB:g} GiB; limits, pins and loopback ports hold "
              f"across {len(dockerfiles)} Dockerfiles")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
