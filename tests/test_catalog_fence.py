"""scripts/verify/catalog-fence.sh decides PASS or FAIL per line (step S22b), without Docker.

K2_FENCE_DIAL replaces the dial with a fake that answers by exit code, and K2_FENCE_INSPECT replaces
the docker inspect of Lakekeeper and of k2-catalog's members with fixed output, so only the
verdict logic runs. The real expectations file's probe rows are parsed by the same loader.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/verify/catalog-fence.sh"
FENCED = "ports \nnetworks k2_k2-catalog,\n"
MEMBERS = "/k2-lakekeeper k2/lakekeeper\n/k2-trino k2/trino\n/k2-minio k2/minio\n"


def fake(tmp_path, name, body):
    f = tmp_path / name
    f.write_text(f"#!/bin/sh\n{body}\n")
    f.chmod(0o755)
    return str(f)


def run(tmp_path, expect="refused", rc=1, origin="k2-net", lakekeeper=FENCED, members=MEMBERS, rows=None):
    if rows is None:
        rows = tmp_path / "rows.yaml"
        rows.write_text(f'expectations:\n  - {{probe: {{from: {origin}, target: "lakekeeper:8181"}}, expect: {expect}}}\n'
                        '  - {principal: p.x, sql: "SELECT 1", expect: denied}\n')
    (tmp_path / "lakekeeper.out").write_text(lakekeeper or "")
    (tmp_path / "members.out").write_text(members or "")
    inspect = fake(tmp_path, "inspect.sh", f'case "$1:{bool(lakekeeper)}:{bool(members)}" in\n'
                   '  lakekeeper:True:*|members:*:True) cat "$(dirname "$0")/$1.out" ;;\n  *) exit 1 ;;\nesac')
    return subprocess.run(["bash", str(SCRIPT), str(rows)], capture_output=True, text=True,
                          env={**os.environ, "K2_FENCE_DIAL": fake(tmp_path, "dial.sh", f"exit {rc}"),
                               "K2_FENCE_INSPECT": inspect, "K2_COMPOSE_PROJECT": "k2"})


def probe_lines(r):
    return [x for x in r.stdout.splitlines() if " -> " in x]


@pytest.mark.parametrize("expect,rc,verdict,says", [
    ("refused", 1, "PASS", "refused or timed out"), ("refused", 0, "FAIL", "connected"),
    ("open", 0, "PASS", "connected"), ("open", 1, "FAIL", "no connection"),
    ("refused", 3, "PASS", "refused (name does not resolve on k2-net)"), ("open", 3, "FAIL", "unresolvable"),
    ("refused", 4, "FAIL", "network k2-net absent"), ("open", 4, "FAIL", "network k2-net absent"),
    ("refused", 125, "FAIL", "could not start (docker exit 125)"),
    ("refused", 126, "FAIL", "could not start (docker exit 126)"),
    ("refused", 127, "FAIL", "could not start (docker exit 127)"), ("open", 125, "FAIL", "could not start"),
])
def test_a_row_passes_only_when_the_dial_matches_its_expectation(tmp_path, expect, rc, verdict, says):
    r = run(tmp_path, expect, rc)
    (line,) = probe_lines(r)  # the non-probe row is not dialled
    assert line.startswith(f"{verdict} k2-net -> lakekeeper:8181") and says in line, r.stdout
    assert r.returncode == (0 if verdict == "PASS" else 1)


def test_the_structure_lines_pass_on_a_fenced_stack(tmp_path):
    r = run(tmp_path)
    assert [x.split()[0] for x in r.stdout.splitlines() if " structure " in f" {x}"] == ["PASS"] * 3, r.stdout
    assert "catalog-fence: 4 PASS, 0 FAIL" in r.stdout and r.returncode == 0


@pytest.mark.parametrize("lakekeeper,members,says", [
    ("ports 8181/tcp,8181/tcp,\nnetworks k2_k2-catalog,\n", MEMBERS, "lakekeeper publishes 8181/tcp"),
    ("ports \nnetworks k2_k2-catalog,k2_k2-net,\n", MEMBERS, "lakekeeper is on k2_k2-catalog,k2_k2-net"),
    (None, MEMBERS, "no running lakekeeper container"),
    (FENCED, MEMBERS + "/k2-grafana k2/grafana\n", "off catalog-fence-allow.txt: k2-grafana=k2/grafana"),
    (FENCED, MEMBERS + "/twin other/trino\n", "twin=other/trino"),
    (FENCED, MEMBERS + "/k2-drop-1 /\n", "k2-drop-1=/"),
    (FENCED, None, "cannot list the members of k2_k2-catalog"),
])
def test_a_breached_structure_fails_and_unresolvable_is_then_no_evidence(tmp_path, lakekeeper, members, says):
    r = run(tmp_path, "refused", 3, lakekeeper=lakekeeper, members=members)
    assert any(x.startswith("FAIL structure") and says in x for x in r.stdout.splitlines()), r.stdout
    (line,) = probe_lines(r)
    assert line.startswith("FAIL k2-net -> lakekeeper:8181") and "proves nothing" in line, r.stdout
    assert r.returncode == 1


def test_an_origin_with_no_network_is_a_failure_not_a_pass(tmp_path):
    assert probe_lines(run(tmp_path, "refused", 5, origin="fetch"))[0].startswith("FAIL fetch ->")


def test_no_probe_rows_is_not_a_pass(tmp_path):
    f = tmp_path / "none.yaml"
    f.write_text('expectations:\n  - {principal: p.x, sql: "SELECT 1", expect: denied}\n')
    assert run(tmp_path, rows=f).returncode == 1  # the structure lines alone do not pass


def test_every_probe_row_of_the_real_expectations_is_dialled(tmp_path):
    real = ROOT / "tests/access/expectations.yaml"
    want = sum(1 for r in yaml.safe_load(real.read_text())["expectations"] if "probe" in r)
    assert want and len(probe_lines(run(tmp_path, rows=real))) == want
