"""docker/lake/init-lake.sh against fakes on PATH: `mc` does nothing, `curl` is Lakekeeper,
listing the warehouses that exist and answering 201 to each create, and logging every create.
The warehouses come from the release's storage plan (compiled/lake/storage-plan.json): every
one but `k2` (created first, on its own) is created once, and a plan that is missing or names
no domain warehouse fails the one-shot rather than leaving Trino's catalogs over nothing."""

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "platform/k2gen/tests/golden/lake/compiled/lake/storage-plan.json"

FAKE_CURL = r"""#!/usr/bin/env bash
out="" body="" url=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out=$2; shift 2 ;; -d) body=$2; shift 2 ;; -w|-X|-H) shift 2 ;; -*) shift ;; *) url=$1; shift ;; esac
done
if [ -z "$body" ]; then cat "$EXISTING"; exit 0; fi     # GET: the warehouse list
printf '%s\n' "$url ${body//$'\n'/}" >> "$LOG"
[ -n "$out" ] && echo '{}' > "$out"
printf 201
"""


def run(tmp: Path, plan: str | None, existing: list[str]) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "curl").write_text(FAKE_CURL)
    (bin_dir / "mc").write_text("#!/usr/bin/env bash\nexit 0\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    (tmp / "existing.json").write_text(json.dumps({"warehouses": [{"name": n} for n in existing]}, separators=(",", ":")))
    plan_path = tmp / "storage-plan.json"
    if plan is not None:
        plan_path.write_text(plan)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "LOG": str(tmp / "log"), "EXISTING": str(tmp / "existing.json"),
           "K2_STORAGE_PLAN": str(plan_path), "MINIO_ROOT_USER": "dummy-user", "MINIO_ROOT_PASSWORD": "dummy-password"}
    done = subprocess.run([shutil.which("bash") or "bash", str(ROOT / "docker/lake/init-lake.sh")],
                          env=env, capture_output=True, text=True, timeout=30)
    log = (tmp / "log").read_text().splitlines() if (tmp / "log").exists() else []
    created = [json.loads(line.split(" ", 1)[1])["warehouse-name"] for line in log if "/management/v1/warehouse" in line]
    return done, created


def test_creates_k2_then_every_domain_warehouse_of_the_storage_plan(tmp_path: Path) -> None:
    done, created = run(tmp_path, PLAN.read_text(), existing=[])
    assert done.returncode == 0, done.stderr
    domains = [w["name"] for w in json.loads(PLAN.read_text())["warehouses"] if w["name"] != "k2"]
    assert domains and created == ["k2", *domains]


def test_a_rerun_skips_what_exists(tmp_path: Path) -> None:
    names = [w["name"] for w in json.loads(PLAN.read_text())["warehouses"]]
    done, created = run(tmp_path, PLAN.read_text(), existing=names)
    assert done.returncode == 0, done.stderr
    assert created == [] and done.stdout.count("exists, skipping create") == len(names)


def test_a_missing_or_empty_plan_fails(tmp_path: Path) -> None:
    done, created = run(tmp_path, None, existing=[])
    assert done.returncode == 1 and "no storage plan" in done.stderr and created == ["k2"]
    only_k2 = json.dumps({"warehouses": [{"name": "k2", "namespaces": [], "remoteSigningEnabled": False}]})
    done, _ = run(tmp_path, only_k2, existing=[])
    assert done.returncode == 1 and "names no domain warehouse" in done.stderr
