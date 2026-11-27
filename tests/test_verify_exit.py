"""scripts/verify/dbt-wrapper.sh's last lines, the exit of the proof (audit 2026-11-27, HIGH 7).

The claims step's lines were piped through `tee` and its exit dropped, so a crash after some PASS
lines exited 0 with the later claims never run. These run the script's own tail, from the claims
step to the end of the file, with `probe` replaced by a stub: no container.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/verify/dbt-wrapper.sh"
START = 'probe claims "$ID1" | tee "$W/claims"\n'
LAST = "claims: every claim run, 0 FAIL"


def tail(stub: str, tmp_path: Path) -> subprocess.CompletedProcess:
    text = SCRIPT.read_text()
    assert text.count(START) == 1, "the claims step moved: point this test at it"
    prelude = f'set -uo pipefail\nW={tmp_path}; T0=$SECONDS; ID1=x\nprobe() {{ {stub}; }}\n'
    return subprocess.run(["bash", "-c", prelude + text[text.index(START):]], capture_output=True, text=True)


def test_a_crash_after_two_pass_lines_exits_non_zero(tmp_path):
    r = tail('echo "PASS one: x"; echo "PASS two: y"; exit 1', tmp_path)
    assert r.returncode != 0 and "2 PASS, 0 FAIL" in r.stdout


def test_an_exit_0_without_the_last_line_exits_non_zero(tmp_path):
    r = tail('echo "PASS one: x"; echo "PASS two: y"', tmp_path)
    assert r.returncode != 0


def test_a_fail_line_exits_non_zero(tmp_path):
    r = tail('echo "PASS one: x"; echo "FAIL two: y"; echo "claims: every claim run, 1 FAIL"; exit 1', tmp_path)
    assert r.returncode != 0


def test_every_claim_run_and_passed_exits_0(tmp_path):
    r = tail(f'echo "PASS one: x"; echo "PASS two: y"; echo "{LAST}"', tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_claims_step_prints_the_line_the_shell_requires():
    assert 'print(f"claims: every claim run, {fails} FAIL"' in SCRIPT.read_text()
