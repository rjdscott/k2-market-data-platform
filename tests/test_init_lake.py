"""docker/lake/init-lake.sh against fakes on PATH: `curl` is Lakekeeper and `mc` is MinIO's admin
API, both keeping their state in one JSON file so a second run sees what the first made, and both
logging every call (argv, and the body or stdin they were handed).

The warehouses come from the release's storage plan (compiled/lake/storage-plan.json): `k2` first,
then every domain warehouse, each with remote signing off, and an existing warehouse that still
signs is switched off in place, never recreated (spike (b): while Lakekeeper signs, an engine's own
key decides nothing). Then one MinIO user and policy per engine (docker/lake/minio/policies/),
the policies rendered from the plan and checked here by what they allow on real-looking keys;
fetch has no user. A scoped pair that is unset, equal to the root pair or shared is refused
before anything changes, and no secret reaches a command line or the output."""

import json
import os
import shutil
import subprocess
import uuid
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "platform/k2gen/tests/golden/lake/compiled/lake/storage-plan.json"
POLICIES = ROOT / "docker/lake/minio/policies"
ENGINES = ["backup", "maintenance", "spark", "tools", "trino"]
ROOT_USER, ROOT_PASSWORD = "root-user", "r00t:p@ss/w%rd"   # characters MC_HOST_<alias> must encode

FAKE_CURL = r"""#!/usr/bin/env python3
import json, os, sys, uuid
args, out, method, data, url = sys.argv[1:], None, "GET", None, None
i = 0
while i < len(args):
    a = args[i]
    if a in ("-o", "-w", "-X", "-H", "-d", "--data-binary"):
        v = args[i + 1]; i += 2
        if a == "-o": out = v
        elif a == "-X": method = v
        elif a == "-d": data = v
        elif a == "--data-binary": data = sys.stdin.read() if v == "@-" else v
        continue
    if not a.startswith("-"): url = a
    i += 1
st = json.load(open(os.environ["STATE"]))
with open(os.environ["LOG"], "a") as log:
    log.write(json.dumps({"tool": "curl", "argv": sys.argv[1:], "method": method, "url": url, "body": data}) + "\n")
def answer(code, body):
    if out: open(out, "w").write(json.dumps(body))
    else: sys.stdout.write(json.dumps(body, separators=(",", ":")))
    if "-w" in args: sys.stdout.write(str(code))
    json.dump(st, open(os.environ["STATE"], "w"))
path = url.split("/management/v1/", 1)[1].split("?")[0]
if method == "GET" and path == "warehouse":
    answer(200, {"warehouses": st["warehouses"]})
elif path == "bootstrap":
    answer(204, {})
elif path == "warehouse":
    b = json.loads(data)
    st["warehouses"].append({"id": "x", "warehouse-id": str(uuid.uuid4()), "name": b["warehouse-name"],
                             "project-id": b["project-id"], "storage-profile": b["storage-profile"]})
    answer(201, {})
elif path.startswith("warehouse/") and path.endswith("/storage"):
    w = next(w for w in st["warehouses"] if w["warehouse-id"] == path.split("/")[1])
    if not os.environ.get("STUBBORN"):
        w["storage-profile"] = json.loads(data)["storage-profile"]
    answer(200, w)
else:
    answer(404, {"error": path})
"""

FAKE_MC = r"""#!/usr/bin/env python3
import json, os, sys, urllib.parse
a = sys.argv[1:]
st = json.load(open(os.environ["STATE"]))
stdin = sys.stdin.read() if a[:3] == ["admin", "user", "add"] else ""
with open(os.environ["LOG"], "a") as log:
    log.write(json.dumps({"tool": "mc", "argv": a, "stdin": stdin}) + "\n")
u = urllib.parse.urlsplit(os.environ.get("MC_HOST_k2", ""))
st["mc_host"] = [urllib.parse.unquote(u.username or ""), urllib.parse.unquote(u.password or ""), u.hostname, u.port]
rc = 0
if a[:3] == ["admin", "policy", "create"]:
    st["policies"][a[4]] = json.load(open(a[5]))
elif a[:3] == ["admin", "user", "add"]:
    st["users"].setdefault(a[4], {"policies": []})["secret"] = stdin.rstrip("\n")
elif a[:3] == ["admin", "user", "info"]:
    p = st["users"][a[4]]["policies"]
    print(json.dumps({"status": "success", "accessKey": a[4], **({"policyName": ",".join(p)} if p else {}),
                      "userStatus": "enabled"}, indent=1))
elif a[:3] == ["admin", "policy", "attach"]:
    p = st["users"][a[6]]["policies"]   # admin policy attach k2 <policy> --user <access key>
    if a[4] in p:
        print("mc: <ERROR> The specified policy change is already in effect.", file=sys.stderr); rc = 1
    p.append(a[4])
json.dump(st, open(os.environ["STATE"], "w"))
sys.exit(rc)
"""


def keys(**override: str) -> dict[str, str]:
    env = {}
    for e in ENGINES:
        env[f"K2_{e.upper()}_S3_ACCESS_KEY"] = f"k2-{e}-key"
        env[f"K2_{e.upper()}_S3_SECRET_KEY"] = f"{e}-secret-0123456789"
    return {**env, **override}


def warehouse(name: str, signing: bool | None) -> dict:
    profile = {"type": "s3", "bucket": "k2-lake", "key-prefix": f"warehouse/{name}"}
    if signing is not None:
        profile["remote-signing-enabled"] = signing
    return {"id": "x", "warehouse-id": str(uuid.uuid5(uuid.NAMESPACE_DNS, name)), "name": name,
            "storage-profile": profile}


def run(tmp: Path, plan: str | None = None, existing: list[dict] | None = None, env: dict | None = None,
        fresh: bool = True) -> tuple[subprocess.CompletedProcess[str], list[dict], dict]:
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "curl").write_text(FAKE_CURL)
    (bin_dir / "mc").write_text(FAKE_MC)
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    state, log = tmp / "state.json", tmp / "log"
    if fresh:
        state.write_text(json.dumps({"warehouses": existing or [], "policies": {}, "users": {}}))
        log.unlink(missing_ok=True)
    start = len(log.read_text().splitlines()) if log.exists() else 0
    plan_path = tmp / "storage-plan.json"
    if plan is not None:
        plan_path.write_text(plan)
    full = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "LOG": str(log), "STATE": str(state),
            "K2_STORAGE_PLAN": str(plan_path), "MINIO_ROOT_USER": ROOT_USER, "MINIO_ROOT_PASSWORD": ROOT_PASSWORD,
            **(keys() if env is None else env)}
    done = subprocess.run([shutil.which("bash") or "bash", str(ROOT / "docker/lake/init-lake.sh")],
                          env=full, capture_output=True, text=True, timeout=30)
    calls = [json.loads(line) for line in log.read_text().splitlines()[start:]] if log.exists() else []
    return done, calls, json.loads(state.read_text())


def plan_names() -> list[str]:
    return [w["name"] for w in json.loads(PLAN.read_text())["warehouses"]]


def creates(calls: list[dict]) -> list[str]:
    return [json.loads(c["body"])["warehouse-name"] for c in calls
            if c["tool"] == "curl" and c["method"] == "POST" and c["url"].endswith("/management/v1/warehouse")]


def updates(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["tool"] == "curl" and c["url"].endswith("/storage")]


def mutations(calls: list[dict]) -> list[dict]:
    return [c for c in calls if (c["tool"] == "curl" and c["method"] != "GET")
            or (c["tool"] == "mc" and c["argv"][:3] != ["admin", "user", "info"])]


# --- warehouses, remote signing off ------------------------------------------------------------

def test_creates_k2_then_every_domain_warehouse_with_remote_signing_off(tmp_path: Path) -> None:
    done, calls, state = run(tmp_path, PLAN.read_text())
    assert done.returncode == 0, done.stderr
    domains = [n for n in plan_names() if n != "k2"]
    assert domains and creates(calls) == ["k2", *domains]
    for c in calls:
        if c["tool"] == "curl" and c["method"] == "POST" and c["url"].endswith("/warehouse"):
            b = json.loads(c["body"])
            assert b["storage-profile"]["remote-signing-enabled"] is False
            assert b["storage-profile"]["key-prefix"] == f"warehouse/{b['warehouse-name']}"
    assert all(w["storage-profile"]["remote-signing-enabled"] is False for w in state["warehouses"])
    # The lake bucket, then the plan's landing bucket: plain, no --with-lock, no versioning.
    assert [c["argv"] for c in calls if c["tool"] == "mc" and c["argv"][0] != "admin"] == \
        [["mb", "--ignore-existing", "k2/k2-lake"], ["mb", "--ignore-existing", "k2/k2-landing"]]


def test_existing_warehouses_that_sign_are_switched_off_in_place_never_recreated(tmp_path: Path) -> None:
    names = plan_names()
    # One that predates the field (Lakekeeper's default is on), one on, one already off.
    existing = [warehouse(names[0], None), warehouse(names[1], True), warehouse(names[2], False)]
    done, calls, state = run(tmp_path, PLAN.read_text(), existing=existing)
    assert done.returncode == 0, done.stderr
    assert creates(calls) == []
    assert not [c for c in calls if c["tool"] == "curl" and c["method"] not in ("GET", "POST")]   # no DELETE, no PUT
    ups = updates(calls)
    assert [c["url"].split("/")[-2] for c in ups] == [w["warehouse-id"] for w in existing[:2]]
    for c, w in zip(ups, existing):
        b = json.loads(c["body"])
        assert b["storage-profile"]["remote-signing-enabled"] is False
        assert b["storage-profile"]["bucket"] == "k2-lake" and b["storage-profile"]["key-prefix"] == f"warehouse/{w['name']}"
        # Lakekeeper does not reuse the old credential: an update without one would leave the warehouse keyless.
        assert b["storage-credential"]["aws-access-key-id"] == ROOT_USER
    assert all(w["storage-profile"]["remote-signing-enabled"] is False for w in state["warehouses"])


def test_a_rerun_changes_no_warehouse(tmp_path: Path) -> None:
    done, _, _ = run(tmp_path, PLAN.read_text())
    assert done.returncode == 0, done.stderr
    done, calls, _ = run(tmp_path, PLAN.read_text(), fresh=False)
    assert done.returncode == 0, done.stderr
    assert creates(calls) == [] and updates(calls) == []
    assert done.stdout.count("remote signing off, skipping") == len(plan_names())


def test_a_warehouse_that_still_signs_after_the_update_fails_the_run(tmp_path: Path) -> None:
    existing = [warehouse(n, True) for n in plan_names()]
    bad = {**keys(), "STUBBORN": "1"}
    done, _, _ = run(tmp_path, PLAN.read_text(), existing=existing, env=bad)
    assert done.returncode != 0 and "still signs" in done.stderr


def test_a_missing_or_empty_plan_fails(tmp_path: Path) -> None:
    done, calls, _ = run(tmp_path, None)
    assert done.returncode == 1 and "no storage plan" in done.stderr and creates(calls) == ["k2"]
    only_k2 = json.dumps({"warehouses": [{"name": "k2", "namespaces": [], "remoteSigningEnabled": False}],
                          "landing": {"bucket": "k2-landing", "prefixes": []}})
    done, _, _ = run(tmp_path, only_k2)
    assert done.returncode == 1 and "names no domain warehouse" in done.stderr


# --- one user and policy per engine --------------------------------------------------------------

def test_one_user_per_engine_each_holding_only_its_own_policy_and_fetch_none(tmp_path: Path) -> None:
    assert sorted(p.stem for p in POLICIES.glob("*.json")) == ENGINES   # fetch.json would make a fetch user
    env = {**keys(), "K2_FETCH_S3_ACCESS_KEY": "k2-fetch-key", "K2_FETCH_S3_SECRET_KEY": "fetch-secret-0123456789"}
    done, _, state = run(tmp_path, PLAN.read_text(), env=env)
    assert done.returncode == 0, done.stderr
    assert {u: v["policies"] for u, v in state["users"].items()} == {f"k2-{e}-key": [f"k2-{e}"] for e in ENGINES}
    assert {u: v["secret"] for u, v in state["users"].items()} == {f"k2-{e}-key": f"{e}-secret-0123456789" for e in ENGINES}
    assert sorted(state["policies"]) == [f"k2-{e}" for e in ENGINES]
    assert state["mc_host"] == [ROOT_USER, ROOT_PASSWORD, "minio", 9000]   # root reaches mc by MC_HOST_k2


def test_a_rerun_makes_the_same_users_and_attaches_nothing_twice(tmp_path: Path) -> None:
    done, _, first = run(tmp_path, PLAN.read_text())
    assert done.returncode == 0, done.stderr
    done, calls, second = run(tmp_path, PLAN.read_text(), fresh=False)
    assert done.returncode == 0, done.stderr
    assert len(first["users"]) == len(ENGINES)
    assert second["users"] == first["users"] and second["policies"] == first["policies"]
    assert not [c for c in calls if c["tool"] == "mc" and c["argv"][:3] == ["admin", "policy", "attach"]]


def test_a_user_holding_another_policy_is_refused(tmp_path: Path) -> None:
    done, _, _ = run(tmp_path, PLAN.read_text())
    assert done.returncode == 0, done.stderr
    state_file = tmp_path / "state.json"
    state = json.loads(state_file.read_text())
    state["users"]["k2-tools-key"]["policies"].append("readwrite")
    state_file.write_text(json.dumps(state))
    done, _, _ = run(tmp_path, PLAN.read_text(), fresh=False)
    assert done.returncode != 0 and "k2-tools" in done.stderr and "readwrite" in done.stderr


def rendered(tmp: Path) -> dict[str, dict]:
    done, _, state = run(tmp, PLAN.read_text())
    assert done.returncode == 0, done.stderr
    assert sorted(state["policies"]) == [f"k2-{e}" for e in ENGINES]
    return {name.removeprefix("k2-"): doc for name, doc in state["policies"].items()}


def allows(policy: dict, action: str, key: str) -> bool:
    """Whether an Allow statement grants `action` on object `bucket/key` (or, for ListBucket, a listing
    of bucket under prefix key). Only Allow and StringLike s3:prefix appear in these policies."""
    bucket, _, rest = key.partition("/")
    for s in policy["Statement"]:
        assert s["Effect"] == "Allow" and set(s) <= {"Effect", "Action", "Resource", "Condition"}
        if action not in s["Action"]:
            continue
        target = f"arn:aws:s3:::{bucket}" if action == "s3:ListBucket" else f"arn:aws:s3:::{key}"
        if not any(fnmatchcase(target, r) for r in s["Resource"]):
            continue
        cond = s.get("Condition")
        if cond is None:
            return True
        assert list(cond) == ["StringLike"] and list(cond["StringLike"]) == ["s3:prefix"]
        if any(fnmatchcase(rest, p) for p in cond["StringLike"]["s3:prefix"]):
            return True
    return False


def test_every_policy_is_object_actions_on_named_buckets_only(tmp_path: Path) -> None:
    allowed = {"s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListBucket"}
    warehouses = plan_names()
    for engine, doc in rendered(tmp_path).items():
        assert "@" not in json.dumps(doc), f"{engine}: a placeholder left unrendered"
        for s in doc["Statement"]:
            assert set(s["Action"]) <= allowed, f"{engine}: {s['Action']}"
            for r in s["Resource"]:
                bucket, _, path = r.removeprefix("arn:aws:s3:::").partition("/")
                assert r.startswith("arn:aws:s3:::") and bucket in ("k2-lake", "k2-landing"), f"{engine}: {r}"
                assert "*" not in bucket and "?" not in r
                if bucket == "k2-lake":   # never the whole lake bucket: one warehouse prefix per resource
                    assert path in [f"warehouse/{w}/*" for w in warehouses] or (path == "" and s["Action"] == ["s3:ListBucket"]), f"{engine}: {r}"


def test_each_key_reaches_its_own_scope_and_nothing_else(tmp_path: Path) -> None:
    pol = rendered(tmp_path)
    domains = [n for n in plan_names() if n != "k2"]
    k2 = "k2-lake/warehouse/k2/station_readings/t/data/f.parquet"
    doms = [f"k2-lake/warehouse/{d}/ns/t/metadata/m.avro" for d in domains]
    landing = "k2-landing/sensor-net/station-readings/2026-11-27/abc.csv"
    outside = ["k2-lake/other/f", "k2-lake/warehouse/k2_extra/f", "k2-lake/warehouse/k2", "other-bucket/warehouse/k2/f"]
    R, W, D = "s3:GetObject", "s3:PutObject", "s3:DeleteObject"

    def can(engine: str, action: str, key: str) -> bool:
        return allows(pol[engine], action, key)

    # spark: writes k2 and the landings; never deletes a landing; nothing on a domain warehouse.
    assert all(can("spark", a, k2) for a in (R, W, D))
    assert can("spark", R, landing) and can("spark", W, landing) and not can("spark", D, landing)
    assert not any(can("spark", a, d) for a in (R, W, D) for d in doms)
    # trino: writes the domain warehouses, reads k2 (the `lake` catalog), no landing.
    assert all(can("trino", a, d) for a in (R, W, D) for d in doms)
    assert can("trino", R, k2) and not can("trino", W, k2) and not can("trino", D, k2)
    assert not any(can("trino", a, landing) for a in (R, W, D))
    # maintenance: reads and deletes in every warehouse (expire, orphan cleanup), writes nothing.
    assert all(can("maintenance", a, x) for a in (R, D) for x in [k2, *doms])
    assert not any(can("maintenance", W, x) for x in [k2, *doms, landing]) and not can("maintenance", R, landing)
    # backup and tools: read everything backed up, write and delete nothing.
    for e in ("backup", "tools"):
        assert all(can(e, R, x) for x in [k2, *doms, landing])
        assert not any(can(e, a, x) for a in (W, D, "s3:AbortMultipartUpload") for x in [k2, *doms, landing])
    for e in ENGINES:
        assert not any(can(e, a, x) for a in (R, W, D) for x in outside), e
    # Listing follows the same scopes.
    L = "s3:ListBucket"
    assert can("spark", L, "k2-lake/warehouse/k2/ns/") and not can("spark", L, f"k2-lake/warehouse/{domains[0]}/")
    assert can("trino", L, f"k2-lake/warehouse/{domains[0]}/ns/") and not can("trino", L, "k2-landing/x/")
    assert not any(can(e, L, "k2-lake/") for e in ENGINES)   # nobody lists the bucket root


# --- refusals and secrets ------------------------------------------------------------------------

def test_an_unset_root_equal_or_shared_pair_is_refused_before_anything_changes(tmp_path: Path) -> None:
    cases = {
        "K2_SPARK_S3_SECRET_KEY": {k: v for k, v in keys().items() if k != "K2_SPARK_S3_SECRET_KEY"},
        "K2_TRINO_S3_ACCESS_KEY": keys(K2_TRINO_S3_ACCESS_KEY=""),
        "K2_TOOLS_S3_ACCESS_KEY equals the root": keys(K2_TOOLS_S3_ACCESS_KEY=ROOT_USER),
        "K2_BACKUP_S3_SECRET_KEY equals the root": keys(K2_BACKUP_S3_SECRET_KEY=ROOT_PASSWORD),
        "K2_TOOLS_S3_ACCESS_KEY is also": keys(K2_TOOLS_S3_ACCESS_KEY="k2-backup-key"),
    }
    for message, env in cases.items():
        done, calls, _ = run(tmp_path, PLAN.read_text(), env=env)
        assert done.returncode != 0 and message in done.stderr, (message, done.stderr)
        assert mutations(calls) == [], message


def test_no_secret_on_a_command_line_or_in_the_output(tmp_path: Path) -> None:
    existing = [warehouse("k2", True)]
    done, calls, _ = run(tmp_path, PLAN.read_text(), existing=existing)
    assert done.returncode == 0, done.stderr
    secrets = [ROOT_PASSWORD] + [v for k, v in keys().items() if k.endswith("_SECRET_KEY")]
    for c in calls:
        assert not any(s in a for s in secrets for a in c["argv"]), c["argv"]
    assert not any(s in done.stdout + done.stderr for s in secrets)
    # They went where they belong: the root pair in Lakekeeper's bodies (stdin), each engine's on mc's stdin.
    assert all(ROOT_PASSWORD in c["body"] for c in calls if c["tool"] == "curl" and c["method"] == "POST" and "warehouse" in c["url"])
    assert sorted(c["stdin"].strip() for c in calls if c["tool"] == "mc" and c["argv"][:3] == ["admin", "user", "add"]) == \
        sorted(v for k, v in keys().items() if k.endswith("_SECRET_KEY"))
