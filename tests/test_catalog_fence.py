"""scripts/verify/catalog-fence.sh decides PASS or FAIL per line (step S22b), without Docker.

K2_FENCE_DIAL replaces the dial with a fake that answers by exit code, and K2_FENCE_INSPECT replaces
the docker inspect of Lakekeeper and of k2-catalog's members with fixed output, so only the
verdict logic runs. The real expectations file's probe rows are parsed by the same loader.
"""

import json
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


def run(tmp_path, expect="refused", rc=1, origin="k2-net", lakekeeper=FENCED, members=MEMBERS, rows=None, images=None):
    if rows is None:
        rows = tmp_path / "rows.yaml"
        rows.write_text(f'expectations:\n  - {{probe: {{from: {origin}, target: "lakekeeper:8181"}}, expect: {expect}}}\n'
                        '  - {principal: p.x, sql: "SELECT 1", expect: denied}\n')
    (tmp_path / "lakekeeper.out").write_text(lakekeeper or "")
    (tmp_path / "members.out").write_text(members or "")
    inspect = fake(tmp_path, "inspect.sh", f'case "$1:{bool(lakekeeper)}:{bool(members)}" in\n'
                   '  lakekeeper:True:*|members:*:True) cat "$(dirname "$0")/$1.out" ;;\n  *) exit 1 ;;\nesac')
    if images is not None:  # the live release's oneshots.json, where the airflow pointer leads
        live = tmp_path / "releases/pointers/airflow/compiled/oneshots.json"
        live.parent.mkdir(parents=True)
        live.write_text(json.dumps({"oneshots": {k: {"image": v} for k, v in images.items()}}))
    return subprocess.run(["bash", str(SCRIPT), str(rows)], capture_output=True, text=True,
                          env={**os.environ, "K2_FENCE_DIAL": fake(tmp_path, "dial.sh", f"exit {rc}"),
                               "K2_FENCE_INSPECT": inspect, "K2_COMPOSE_PROJECT": "k2",
                               "K2_RELEASES": str(tmp_path / "releases")})


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
    # a host port only another compose project publishes (the live stack beside a rehearsal) is not this stack's
    ("refused", 6, "PASS", "publishes that port, not"), ("open", 6, "FAIL", "publishes that port, expected open"),
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


IMAGES = {"dbt-runner": "dbt-runner:5a6b7c8d9e0f", "fetch": "fetch:4008294739ab", "tools": "tools:0a1b2c3d4e5f"}
DBT_ONESHOT = "/k2-dbt-runner-ecb_fx_rates-1 / k2/dbt-runner dbt-runner:5a6b7c8d9e0f"


@pytest.mark.parametrize("images", [IMAGES, None], ids=["image checked", "no live release"])
def test_a_labelled_dbt_runner_one_shot_of_this_project_is_admitted(tmp_path, images):
    """The live failure of 2026-11-27: the operator's dbt-runner, started by `docker run` with no
    compose labels, was counted an intruder. Labelled by the operator, it is one of the stack's own."""
    r = run(tmp_path, members=MEMBERS + DBT_ONESHOT + "\n", images=images)
    (line,) = [x for x in r.stdout.splitlines() if " holds " in x]
    assert line.startswith("PASS structure k2_k2-catalog holds only") and "1 one-shots admitted" in line, r.stdout
    assert ("images as the live release" if images else "images unchecked") in line and r.returncode == 0


@pytest.mark.parametrize("member", [
    "/k2-fetch-currency_master-1 / k2/fetch fetch:4008294739ab",  # fetch is egress only: never on the catalog
    "/k2-dbt-runner-ecb_fx_rates-1 / other/dbt-runner dbt-runner:5a6b7c8d9e0f",  # another project's
    "/evil / k2/dbt-runner dbt-runner:5a6b7c8d9e0f",  # the right labels, not the operator's name
    "/k2-dbt-runner-ecb_fx_rates / k2/dbt-runner dbt-runner:5a6b7c8d9e0f",  # no try number
    "/k2-tools-ecb_fx_rates-1 / k2/dbt-runner dbt-runner:5a6b7c8d9e0f",  # a name of another kind
    "/k2-dbt-runner-ecb_fx_rates-1 / / dbt-runner:5a6b7c8d9e0f",  # no labels: the shape before the operator set them
    "/k2-dbt-runner-ecb_fx_rates-1 /",  # the old inspect format, no labels
    "/k2-dbt-runner-ecb_fx_rates-1 / k2/dbt-runner alpine:3",  # not the image the live release gives the kind
    "/k2-lakekeeper-x-1 / k2/lakekeeper alpine:3",  # an allowed service, but no one-shot kind of the release
    "/k2-dbt-runner-x-1 other/x k2/dbt-runner dbt-runner:5a6b7c8d9e0f",  # compose labels: a service, judged as one
])
def test_anything_else_off_the_list_still_fails_the_structure(tmp_path, member):
    r = run(tmp_path, "refused", 3, members=MEMBERS + member + "\n", images=IMAGES)
    name = member.split()[0].lstrip("/")
    assert any(x.startswith("FAIL structure") and f"off catalog-fence-allow.txt: {name}=" in x
               for x in r.stdout.splitlines()), r.stdout
    assert r.returncode == 1


def test_an_origin_with_no_network_is_a_failure_not_a_pass(tmp_path):
    assert probe_lines(run(tmp_path, "refused", 5, origin="tools"))[0].startswith("FAIL tools ->")


def test_a_fetch_row_is_dialled_from_k2_egress_the_network_fetch_runs_on(tmp_path):
    """The real dial, with `docker` faked on PATH: a `fetch` row starts its probe container on
    <project>_k2-egress (F5/R7), and a name that does not resolve there is refused."""
    log = tmp_path / "docker.log"
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    fake(bin_, "docker", f'echo "$*" >> {log}\ncase "$1" in run) echo "nc: bad address \'lakekeeper\'"; exit 1 ;; esac\nexit 0')
    rows = tmp_path / "rows.yaml"
    rows.write_text('expectations:\n  - {probe: {from: fetch, target: "lakekeeper:8181"}, expect: refused}\n')
    (tmp_path / "lakekeeper.out").write_text(FENCED)
    (tmp_path / "members.out").write_text(MEMBERS)
    inspect = fake(tmp_path, "inspect.sh", 'cat "$(dirname "$0")/$1.out"')
    r = subprocess.run(["bash", str(SCRIPT), str(rows)], capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "K2_FENCE_INSPECT": inspect,
                            "K2_COMPOSE_PROJECT": "k2"})
    calls = log.read_text().splitlines()
    assert "network inspect k2_k2-egress" in calls, calls
    assert [c for c in calls if c.startswith("run ")][0].split("--network ")[1].split()[0] == "k2_k2-egress", calls
    assert probe_lines(r) == ["PASS fetch -> lakekeeper:8181 refused (name does not resolve on fetch)"], r.stdout


def test_no_probe_rows_is_not_a_pass(tmp_path):
    f = tmp_path / "none.yaml"
    f.write_text('expectations:\n  - {principal: p.x, sql: "SELECT 1", expect: denied}\n')
    assert run(tmp_path, rows=f).returncode == 1  # the structure lines alone do not pass


def test_every_probe_row_of_the_real_expectations_is_dialled(tmp_path):
    real = ROOT / "tests/access/expectations.yaml"
    want = sum(1 for r in yaml.safe_load(real.read_text())["expectations"] if "probe" in r)
    assert want and len(probe_lines(run(tmp_path, rows=real))) == want
