"""A multi-line `run:` in CI fails for the right reason. GitHub runs one with no `shell:` as
`bash -e {0}`: no pipefail, so `x=$(a | tail -1)` takes tail's status and a failing `a` passes."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
STEPS = [(job, s) for job, j in yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"].items()
         for s in j["steps"]]


def test_every_multi_line_run_sets_pipefail_or_names_its_shell():
    loose = [f"{job}: {s.get('name') or s['run'][:40]}" for job, s in STEPS
             if "\n" in s.get("run", "").strip() and "shell" not in s
             and not s["run"].lstrip().startswith("set -euo pipefail")]
    assert loose == []


@pytest.mark.parametrize("validate,code,made", [
    ("echo noise; exit 1", 1, False),  # a failure after a stray stdout line: the step fails, make never runs
    ("echo /nonexistent-release", 1, False),  # a last line that is no directory
    ('mkdir -p "$1/r"; echo "$1/r"', 0, True),
])
def test_the_access_verify_step_fails_when_ci_validate_does(tmp_path, validate, code, made):
    (run,) = [s["run"] for job, s in STEPS if s.get("name") == "access-verify on this tree's release"]
    (tmp_path / "scripts/verify").mkdir(parents=True)
    (tmp_path / "scripts/verify/ci-validate.sh").write_text(validate + "\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/make").write_text('#!/bin/sh\necho "$@" > made\n')
    (tmp_path / "bin/make").chmod(0o755)
    r = subprocess.run(["bash", "-e", "-c", run], cwd=tmp_path, capture_output=True, text=True,
                       env={**os.environ, "RUNNER_TEMP": str(tmp_path), "PATH": f"{tmp_path}/bin:{os.environ['PATH']}"})
    assert r.returncode == code and (tmp_path / "made").exists() == made, r.stderr
