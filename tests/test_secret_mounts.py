"""No compose service mounts more of ${K2_SECRETS} than the files it reads (ADR-060), and no
throwaway client container mounts the checkout, whose .env holds the live stack's secrets.

tls/ holds ca.key, which no container may ever see (k2gen.model.SECRET_FILES gives it no
mount), so a service mounts tls/ca.pem or tls/trino.pem by name and never the directory;
nothing mounts ${K2_SECRETS} itself, ca.key or canary/ (read on the host only). Read from
docker-compose.yml's volumes, short and long syntax, so "never mount tls/" is enforced here
rather than advised in a runbook.
"""

import re
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"
# ${K2_SECRETS}, ${K2_SECRETS:-<default>} (whose default may hold ${HOME}), or a literal .../k2-secrets.
ROOT = re.compile(r"\$\{K2_SECRETS(?::-(?:[^${}]|\$\{[^}]*\})*)?\}|[^:]*/k2-secrets(?=/|$)")
NAMED_TLS = {"tls/ca.pem", "tls/trino.pem"}


def source(volume: object) -> str:
    """A volume's source: the long syntax's `source`, or the short syntax up to its first `:`
    outside a ${...} (whose default may hold one)."""
    if isinstance(volume, dict):
        return str(volume.get("source", ""))
    text, depth = str(volume), 0
    for i, c in enumerate(text):
        depth += (c == "{") - (c == "}")
        if c == ":" and depth == 0:
            return text[:i]
    return text


def secret_mount_problems(compose: dict) -> list[str]:
    found = []
    for name, svc in (compose.get("services") or {}).items():
        for v in svc.get("volumes") or []:
            m = ROOT.match(source(v))
            if not m:
                continue
            under = source(v)[m.end():].strip("/")
            parts = under.split("/") if under else []
            if not parts:
                found.append(f"{name} mounts ${{K2_SECRETS}} itself")
            elif parts[0] == "tls" and under not in NAMED_TLS:
                found.append(f"{name} mounts {under or 'tls'}: only {', '.join(sorted(NAMED_TLS))}, each by name")
            elif parts[0] == "canary" or "ca.key" in parts:
                found.append(f"{name} mounts {under}, which no container reads")
    return found


def test_the_compose_file_mounts_no_secret_directory_it_should_not():
    assert secret_mount_problems(yaml.safe_load(COMPOSE.read_text())) == []


@pytest.mark.parametrize("volume, named", [
    ("${K2_SECRETS:-${HOME}/k2-secrets}/tls:/run/k2-secrets/tls:ro", "tls"),
    ("${K2_SECRETS:-${HOME}/k2-secrets}/tls/:/run/k2-secrets/tls:ro", "tls"),
    ("${K2_SECRETS}/tls/ca.key:/run/k2-secrets/ca.key:ro", "tls/ca.key"),
    ("${K2_SECRETS:-${HOME}/k2-secrets}:/run/k2-secrets:ro", "itself"),
    ("${HOME}/k2-secrets/tls:/run/k2-secrets/tls:ro", "tls"),
    ("${K2_SECRETS}/canary:/run/canary:ro", "canary"),
    ({"type": "bind", "source": "${K2_SECRETS:-${HOME}/k2-secrets}/tls", "target": "/run/k2-secrets/tls"}, "tls"),
])
def test_each_forbidden_mount_is_named(volume, named):
    problems = secret_mount_problems({"services": {"trino": {"volumes": [volume]}}})
    assert len(problems) == 1 and named in problems[0], problems


@pytest.mark.parametrize("volume", [
    "${K2_SECRETS:-${HOME}/k2-secrets}/tls/ca.pem:/run/k2-secrets/tls/ca.pem:ro",
    "${K2_SECRETS:-${HOME}/k2-secrets}/tls/trino.pem:/run/k2-secrets/tls/trino.pem:ro",
    "${K2_SECRETS:-${HOME}/k2-secrets}/trino:/run/k2-secrets/trino:ro",
    "${K2_SECRETS:-${HOME}/k2-secrets}/alertmanager:/run/k2-secrets:ro",
    "./docker/trino/etc:/etc/trino:ro",
])
def test_a_named_file_or_another_directory_passes(volume):
    assert secret_mount_problems({"services": {"x": {"volumes": [volume]}}}) == []


@pytest.mark.parametrize("script", ["scripts/lib/throwaway-lake.sh", "scripts/verify/identity.sh"])
def test_no_throwaway_client_mounts_the_checkout_with_its_env(script):
    """The one-off and tools containers get the directories they read, read-only, never the
    checkout itself: run against the live stack, that would hand them the live .env (the review
    of S13, M3)."""
    text = (COMPOSE.parent / script).read_text()
    whole = re.findall(r'-v "\$(?:REPO|PWD|ROOT)(?:/\.?)?:[^"]*"', text)
    assert not whole, whole
    for m in re.findall(r'-v "\$REPO/([^":]+):(/src/[^":]+)(:ro)?"', text) + re.findall(r'-v "\$REPO/\$d:(/src/\$d)(:ro)"', text):
        assert m[-1] == ":ro", m
