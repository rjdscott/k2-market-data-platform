#!/usr/bin/env python3
"""The CI `compose` job's budget and pinning gate, runnable locally:

    python3 scripts/check-budget.py

Resolves the compose file plain, with `--profile '*'` (every service of every
profile), and with `--profile <name>` for each profile a service names; prints
the steady default, the bootstrap default and each profile's bootstrap (the
default plus that profile's services) and exits 1 on any of:

- the bootstrap default (every default-profile service, one-shots included,
  because they run beside the rest at `docker compose up`; each service's
  limits times its `deploy.replicas`, which compose also sets from `scale`) above 25 CPU /
  48 GiB, the ceiling ADR-045 sets in place of ADR-034's 20 CPU / 36 GiB
  (which replaced ADR-010's 16 CPU / 40 GB). The steady default and the
  profiled bootstraps are reported only. One profile is named today:
  `hot-gold`, the paused ClickHouse tier (ADR-057), so three lines print, the
  last being the default plus clickhouse.
- a service under any profile that does not declare both a CPU and a memory
  limit, so a service behind a profile cannot ship without one.
- an image that is not pinned (see `unpinned`): a compose image in any
  profile (one compose builds is exempt, its Dockerfile is checked), a
  Dockerfile FROM or COPY --from, a `docker run` image in the Makefile or a
  shell script.
- a `pip install` package in a Dockerfile, or a `uv run --with` dependency in
  the Makefile, a shell script, a scripts/ Python file or a workflow, with no
  `==` version.
- a GitHub Action not pinned by its full commit SHA with the version in a
  trailing comment (`uses: owner/repo@<40 hex> # v1.2.3`), first-party
  `actions/*` included: a tag can be moved, a commit cannot.
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
import shlex
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


# Images whose releases are one number, so `484` is a release, not a series.
ONE_NUMBER = {"trinodb/trino"}


def unpinned(image: str) -> bool:
    """A digest pins. A tag pins when it names a release, not a series: its
    first number carries a minor version (24.3-alpine, v3.2.0,
    slim-3.3.2-python3.12), or it is a dated release (MinIO's
    RELEASE.2025-09-07T16-13-09Z), or the image is in ONE_NUMBER. Unpinned: no
    tag, a word (latest, nonroot), a bare major (1-bookworm, 15-alpine,
    3-python3.12). A major.minor still takes patch releases; that is the
    floor, not a lockfile."""
    if "@sha256:" in image:
        return False
    if ":" not in image.rsplit("/", 1)[-1]:  # a registry port (host:5000/x) is not a tag
        return True
    repo, tag = image.rsplit(":", 1)
    if re.search(r"\d{4}-\d{2}-\d{2}", tag):
        return False
    first = re.search(r"\d+(?:\.\d+)*", tag)
    if not first:
        return True
    return "." not in first.group(0) and repo.removeprefix("docker.io/").removeprefix("library/") not in ONE_NUMBER


def dockerfile_problems(path: str, text: str) -> list[str]:
    problems, stages = [], {"scratch"}
    for line in text.replace("\\\n", " ").splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        if words[0].upper() == "FROM":
            args = [w for w in words[1:] if not w.startswith("--")]
            if args[0] not in stages and unpinned(args[0]):
                problems.append(f"{path}: FROM {args[0]} is not pinned")
            if len(args) == 3 and args[1].upper() == "AS":
                stages.add(args[2])
        if words[0].upper() == "COPY":
            for w in words[1:]:
                source = w.removeprefix("--from=")
                if w.startswith("--from=") and source not in stages and not source.isdigit() and unpinned(source):
                    problems.append(f"{path}: COPY --from={source} is not pinned")
        # ponytail: pip only. apt-get, cargo and the jar downloads are not
        # checked; cargo-chef carries --version and the jars a sha256 already.
        for m in re.finditer(r"pip3? install\s+([^&;|]*)", line):
            for pkg in m.group(1).split():
                if not pkg.startswith("-") and "==" not in pkg:
                    problems.append(f"{path}: pip install {pkg} has no == version")
    return problems


def check(
    default: dict, profiles: dict[str, dict], every: dict, dockerfiles: list[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """(report lines, failures). Pure, so a test can hand it dicts. `profiles`
    maps each profile name to its `--profile <name>` resolve, reported in that
    order; `every` is the `--profile '*'` resolve, where limits and pins are
    held, so a service behind any profile is checked."""
    steady = {n: s for n, s in default.items() if s.get("restart") != "no"}
    report = []
    for label, services in (
        ("steady default", steady),
        ("bootstrap default", default),
        *((f"bootstrap {name}", services) for name, services in profiles.items()),
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
        if svc.get("image") not in built(every) and unpinned(svc.get("image", "")):
            failures.append(f"service {name}: image {svc.get('image') or '(none)'!r} is not pinned")
    for path, text in dockerfiles:
        failures += dockerfile_problems(path, text)
    return report, failures


# `docker run` options that take the next word as their value.
RUN_VALUE_FLAGS = {"-v", "--volume", "-w", "--workdir", "-e", "--env", "--env-file", "--network", "--net",
                   "-u", "--user", "--entrypoint", "--name", "-p", "--publish", "--ipc", "--mount", "--platform",
                   "-l", "--label", "--add-host", "--cpus", "-m", "--memory", "--tmpfs", "--cap-add", "--cap-drop",
                   "--security-opt", "--ulimit", "-h", "--hostname", "--device", "--pull", "--shm-size", "--pid",
                   "--restart", "--gpus", "--log-driver", "--log-opt", "--group-add", "--dns", "--expose"}
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
PINNED_USES = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
SUBST = re.compile(r"\$\$?\([^()]*\)")
WITH = re.compile(r"--with[ =]+(\"[^\"]*\"|'[^']*'|\S+)")


def built(services: dict) -> set[str]:
    """Images compose builds from this repo: their Dockerfiles are checked, and
    their tag names our own build, not a release someone else can move."""
    return {s["image"] for s in services.values() if s.get("build") and s.get("image")}


def run_images(text: str) -> list[str]:
    """The image of every `docker run` on a line that is not a comment, with
    backslash continuations joined. A variable ($IMAGE) is skipped: the value
    it holds is checked where it is set (compose, for the test scripts)."""
    images = []
    for line in text.replace("\\\n", " ").splitlines():
        if line.lstrip().startswith("#") or "docker run" not in line:
            continue
        rest = line.split("docker run", 1)[1]
        while SUBST.search(rest):  # $(shell id -u), $$(id -u): one word
            rest = SUBST.sub("$VAR", rest)
        try:
            words = shlex.split(rest)
        except ValueError:
            words = rest.split()
        take_value = False
        for w in words:
            if take_value:
                take_value = False
            elif w.startswith("-"):
                take_value = w in RUN_VALUE_FLAGS  # --flag=value carries its own
            else:
                if not w.startswith("$"):
                    images.append(w)
                break
    return images


def source_pin_problems(path: str, text: str, local: frozenset = frozenset()) -> list[str]:
    """Pins outside Dockerfiles and compose: `docker run` images in the
    Makefile and shell (`local`: images compose builds), uv dependencies, and
    workflow Actions."""
    problems = []
    if path == "Makefile" or path.endswith(".sh"):
        problems += [f"{path}: docker run {i} is not pinned" for i in run_images(text)
                     if i not in local and unpinned(i)]
    for n, line in enumerate(text.splitlines(), 1):
        for dep in WITH.findall(line):
            if "==" not in dep:
                problems.append(f"{path}:{n}: uv dependency {dep} has no == version")
        m = USES.match(line) if path.startswith(".github/") else None
        if m and not m.group(1).startswith("./"):
            ref, comment = m.group(1), m.group(2).strip()
            if ref.startswith("docker://"):
                if unpinned(ref.removeprefix("docker://")):
                    problems.append(f"{path}:{n}: uses {ref} is not pinned")
            elif not PINNED_USES.match(ref) or not re.match(r"#\s*v?\d", comment):
                problems.append(f"{path}:{n}: uses {ref} is not a full commit SHA with its version in a comment")
    return problems


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


# Archived or immutable: legacy/ is built by nothing, docs/research/ holds the
# spikes as they ran.
NOT_BUILT = ("legacy/", "docs/research/")


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return [p for p in out.split("\0") if p and not p.startswith(NOT_BUILT) and (ROOT / p).is_file()]


def main() -> int:
    files = tracked()
    dockerfiles = [(p, (ROOT / p).read_text()) for p in files
                   if Path(p).name.startswith("Dockerfile") and not p.endswith(".dockerignore")]
    sources = [p for p in files if p == "Makefile" or p.endswith(".sh") or p.startswith(".github/workflows/")
               or (p.startswith("scripts/") and p.endswith(".py"))]
    every = compose_services("--profile", "*")
    names = sorted({name for svc in every.values() for name in svc.get("profiles") or []})
    report, failures = check(
        compose_services(), {name: compose_services("--profile", name) for name in names}, every, dockerfiles)
    failures += exposure_problems(every)
    for p in sources:
        failures += source_pin_problems(p, (ROOT / p).read_text(), frozenset(built(every)))
    print("\n".join(report))
    for f in failures:
        print(f"FAIL: {f}")
    if not failures:
        print(f"PASS: bootstrap default within {CPU_CEILING:g} CPU / {MEM_CEILING / GIB:g} GiB; limits, pins and loopback ports hold "
              f"across {len(dockerfiles)} Dockerfiles and {len(sources)} scripts and workflows")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
