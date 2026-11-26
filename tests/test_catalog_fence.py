"""scripts/verify/catalog-fence.sh decides PASS or FAIL per probe row (step S22b), without Docker.

K2_FENCE_DIAL replaces the dial with a fake that answers by exit code, so only the row-to-verdict
logic runs. The real expectations file's probe rows are parsed by the same loader.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/verify/catalog-fence.sh"


def run(tmp_path, expect, rc, origin="k2-net"):
    rows = tmp_path / "rows.yaml"
    rows.write_text(f'- {{probe: {{from: {origin}, target: "lakekeeper:8181"}}, expect: {expect}}}\n'
                    '- {principal: p.x, sql: "SELECT 1", expect: denied}\n')
    fake = tmp_path / "dial.sh"
    fake.write_text(f"#!/bin/sh\nexit {rc}\n")
    fake.chmod(0o755)
    return subprocess.run(["bash", str(SCRIPT), str(rows)], capture_output=True, text=True,
                          env={**os.environ, "K2_FENCE_DIAL": str(fake)})


@pytest.mark.parametrize("expect,rc,verdict,code", [
    ("refused", 1, "PASS", 0), ("refused", 0, "FAIL", 1),
    ("open", 0, "PASS", 0), ("open", 1, "FAIL", 1),
    ("refused", 3, "PASS", 0), ("open", 3, "FAIL", 1),
    ("refused", 4, "PASS", 0), ("open", 4, "FAIL", 1),
])
def test_a_row_passes_only_when_the_dial_matches_its_expectation(tmp_path, expect, rc, verdict, code):
    r = run(tmp_path, expect, rc)
    assert r.stdout.splitlines()[0].startswith(f"{verdict} k2-net -> lakekeeper:8181"), r.stdout
    assert r.returncode == code
    assert "1 PASS, 0 FAIL" in r.stdout or "0 PASS, 1 FAIL" in r.stdout  # the non-probe row is not dialled


def test_an_origin_with_no_network_is_a_failure_not_a_pass(tmp_path):
    assert run(tmp_path, "refused", 5, origin="fetch").stdout.startswith("FAIL fetch ->")


def test_no_probe_rows_is_not_a_pass(tmp_path):
    f = tmp_path / "none.yaml"
    f.write_text('- {principal: p.x, sql: "SELECT 1", expect: denied}\n')
    assert subprocess.run(["bash", str(SCRIPT), str(f)], capture_output=True,
                          env={**os.environ, "K2_FENCE_DIAL": "true"}).returncode == 1


def test_every_probe_row_of_the_real_expectations_is_dialled(tmp_path):
    real = ROOT / "tests/access/expectations.yaml"
    want = sum(1 for r in yaml.safe_load(real.read_text()) if "probe" in r)
    fake = tmp_path / "dial.sh"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    r = subprocess.run(["bash", str(SCRIPT), str(real)], capture_output=True, text=True,
                       env={**os.environ, "K2_FENCE_DIAL": str(fake)})
    lines = [x for x in r.stdout.splitlines() if x.split()[0] in ("PASS", "FAIL")]
    assert len(lines) == want  # the real file has no probe row yet (S20 SKIPs them): the count follows it
