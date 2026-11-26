"""scripts/drop.sh against a fake `docker` on PATH. The fake plays the stack:
`inspect` finds k2-minio's network and mounts, `run` starts the helper (and
refuses unless the MinIO credential is in its environment), and `exec <helper>`
is either curl against a fake Lakekeeper catalog (world.json) or mc against a
fake MinIO (a directory per bucket, plus a version list for a versioned bucket).
A catalog drop with purge deletes the table's files, as Lakekeeper's purge task
does. The mc subcommands and their exit codes are the ones measured on the
pinned image, RELEASE.2025-08-13, on 2026-11-26: `ls --recursive` of a missing
prefix prints nothing and exits 0, `ls` of a missing bucket exits 1, `mirror`
refuses to overwrite a differing file with a JSON error line and exit 0.
Switches in the environment break one step each, so every abort is shown to
leave the catalog, the storage and the drop untouched."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
USER = "dummy-access-key-5e1d77"
PASSWORD = "dummy-secret-value-c09a3f61"
LOC = "s3://k2-lake/warehouse/k2/u-{}"
FILES = {"data/p=1/a.parquet": "rows-a", "metadata/00001-a.metadata.json": "{}",
         "metadata/00002-b.metadata.json": '{"v": 2}', "metadata/snap-1.avro": "m"}
OBJECTS, BYTES = len(FILES), sum(len(b) for b in FILES.values())

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys, urllib.parse as up

out = lambda d: print(json.dumps(d, separators=(",", ":")))   # compact, as mc prints it

A, E = sys.argv[1:], os.environ
with open(E["LOG"], "a") as f:
    f.write(json.dumps(A) + "\n")
WORLD, MINIO, STATE = pathlib.Path(E["WORLD"]), pathlib.Path(E["MINIO"]), pathlib.Path(E["STATE"])
w = json.loads(WORLD.read_text())
save = lambda: WORLD.write_text(json.dumps(w))

if A[0] == "inspect":
    print(E.get("MINIO_MOUNT", "/var/lib/docker/volumes/minio-data/_data") if "Mounts" in A[2] else "k2-test-net ")
    sys.exit(0)
if A[0] == "rm":
    sys.exit(0)
if A[0] == "run":
    if not E.get("MC_HOST_k2", "").endswith(":" + E["WANT_PASSWORD"] + "@minio:9000"):
        sys.exit("credential not in the environment")
    STATE.write_text(next((a.split("src=")[1].split(",")[0] for a in A if a.startswith("type=bind")), ""))
    print("cid")
    sys.exit(0)
assert A[0] == "exec", A
backup = STATE.read_text()
prog, args = A[2], [backup + a[len("/backup"):] if a.startswith("/backup") else a for a in A[3:]]

def route(method, url):
    u = up.urlsplit(url)
    q, parts = up.parse_qs(u.query), u.path.strip("/").split("/")
    if u.path == "/management/v1/warehouse":
        return 200, {"warehouses": [{"name": n, "id": d["id"], "storage-profile": {
            "type": "s3", "bucket": d["bucket"], "key-prefix": d["prefix"]}} for n, d in w["warehouses"].items()]}
    if u.path == "/catalog/v1/config":
        return 200, {"defaults": {"prefix": w["warehouses"][q["warehouse"][0]]["id"]}}
    spaces = next(d for d in w["warehouses"].values() if d["id"] == parts[2])["namespaces"]
    if len(parts) == 4:
        return 200, {"namespaces": [] if "parent" in q else [[n] for n in spaces]}
    ns = up.unquote(parts[4])
    if ns not in spaces:
        return 404, {}
    if len(parts) == 5:
        if method == "DELETE":
            if spaces[ns]:
                return 409, {}
            del spaces[ns]
            save()
            return 204, None
        return 200, {"namespace": [ns]}
    kind = parts[5][:-1]
    if len(parts) == 6:
        return 200, {"identifiers": [{"namespace": [ns], "name": n} for n, o in spaces[ns].items() if o[0] == kind]}
    o = spaces[ns].get(parts[6])
    if not o or o[0] != kind:
        return 404, {}
    if method == "DELETE":
        assert q.get("purgeRequested") == ["true"], url
        del spaces[ns][parts[6]]
        save()
        if not E.get("NO_PURGE"):
            shutil.rmtree(MINIO / o[1][5:], ignore_errors=True)
        return 204, None
    meta = o[2] if len(o) > 2 else o[1] + "/metadata/00002-b.metadata.json"
    return 200, {"metadata-location": meta, "metadata": {"location": o[1]},
                 "config": {"s3.access-key-id": E["WANT_USER"], "s3.secret-access-key": E["WANT_PASSWORD"]}}

def curl(args):
    method, url, fail, code_only, i = "GET", None, False, False, 0
    while i < len(args):
        a = args[i]
        if a in ("-X", "-o", "-w"):
            method = args[i + 1] if a == "-X" else method
            code_only |= a == "-w"
            i += 2
            continue
        if a.startswith("-"):
            fail |= "f" in a
        else:
            url = a
        i += 1
    code, body = route(method, url)
    if code_only:
        print(code, end="")
    elif code >= 400 and fail:
        sys.exit(f"curl: (22) The requested URL returned error: {code}")
    else:
        print(json.dumps(body))

def files(root):
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []

def mc(args):
    vid = args[args.index("--version-id") + 1] if "--version-id" in args else None
    flags = {a for a in args if a.startswith("--")}
    pos = [a for a in args if not a.startswith("--") and a != vid]
    if pos[0] == "version":
        b = pos[2][3:]
        out({"status": "success", "versioning": {"status": "Enabled" if b in w["versioned"] else ""}})
    elif pos[0] == "ls" and "--recursive" not in flags:
        sys.exit(0 if (MINIO / pos[1][3:]).is_dir() else 1)
    elif pos[0] == "ls" and "--versions" in flags:
        b, _, prefix = pos[1][3:].partition("/")
        for v in w["versioned"].get(b, []):
            if v["key"].startswith(prefix):
                out({"status": "success", "type": "file", "key": v["key"][len(prefix):], "size": len(v["body"]),
                                  "versionId": v["id"], **({"isDeleteMarker": True} if v.get("dm") else {})})
    elif pos[0] == "ls":
        root = MINIO / pos[1][3:]
        for p in files(root):
            out({"status": "success", "type": "file", "key": str(p.relative_to(root)), "size": p.stat().st_size})
    elif pos[0] == "mirror":
        src, dst = MINIO / pos[1][3:], pathlib.Path(pos[2])
        for n, p in enumerate(files(src)):
            t = dst / p.relative_to(src)
            if n == 0 and E.get("SKIP"):
                continue
            if not t.exists():
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(p.read_bytes()[:-1] if n == 0 and E.get("TRUNCATE") else p.read_bytes())
                out({"status": "success", "source": str(p)})
            elif t.read_bytes() != p.read_bytes():
                out({"status": "error", "error": {"message": "Overwrite not allowed"}})
        if E.get("GROW"):   # a writer commits while the copy runs
            (src / "data/p=1/late.parquet").write_text("late")
    elif pos[0] == "cp":
        b, _, key = pos[1][3:].partition("/")
        v = next(v for v in w["versioned"][b] if v["key"] == key and v["id"] == vid)
        t = pathlib.Path(pos[2])
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(v["body"])
    elif pos[0] == "rb":
        b = pos[1][3:]
        shutil.rmtree(MINIO / b)
        w["versioned"].pop(b, None)
        save()
    else:
        sys.exit(f"fake mc: unhandled {args}")

curl(args) if prog == "curl" else mc(args)
'''


@pytest.fixture
def stack(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/drop.sh", repo / "scripts/drop.sh")
    (repo / ".env").write_text(f"MINIO_ROOT_USER={USER}\nMINIO_ROOT_PASSWORD={PASSWORD}\nOTHER_PASSWORD=x\n")
    world = {"warehouses": {"k2": {"id": "p-k2", "bucket": "k2-lake", "prefix": "warehouse/k2", "namespaces": {
        "spike": {"t1": ["table", LOC.format("t1")], "v1": ["view", LOC.format("v1")]},
        "gold": {"bars": ["table", LOC.format("bars")]},
    }}}, "versioned": {"landing": [
        {"key": "a.txt", "id": "va2", "body": "new a"}, {"key": "a.txt", "id": "va1", "body": "old a"},
        {"key": "gone.txt", "id": "vg2", "body": "", "dm": True}, {"key": "gone.txt", "id": "vg1", "body": "gone body"},
    ]}}
    (tmp_path / "world.json").write_text(json.dumps(world))
    for name in ("t1", "v1", "bars"):
        for f, body in FILES.items():
            p = tmp_path / "minio" / LOC.format(name)[5:] / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
    (tmp_path / "minio/landing").mkdir(parents=True)
    (tmp_path / "minio/landing/a.txt").write_text("new a")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/docker").write_text(FAKE_DOCKER)
    (tmp_path / "bin/docker").chmod(0o755)
    (tmp_path / "state").write_text("")
    return tmp_path


def run(stack, target, **env):
    base = {"K2_BACKUP_DIR": str(stack / "k2-backup"), "K2_BACKUP_MIN_FREE_GIB": "0", "K2_DROP_PURGE_WAIT_S": "0"}
    if target is not None:
        base["TARGET"] = target
    inherited = {k: v for k, v in os.environ.items() if k not in ("TARGET", "DRY_RUN")}
    full = {**inherited, "PATH": f"{stack / 'bin'}:{os.environ['PATH']}", "LOG": str(stack / "docker.log"),
            "WORLD": str(stack / "world.json"), "MINIO": str(stack / "minio"), "STATE": str(stack / "state"),
            "WANT_USER": USER, "WANT_PASSWORD": PASSWORD, **base, **env}
    return subprocess.run(["bash", str(stack / "repo/scripts/drop.sh")], capture_output=True, text=True,
                          timeout=120, env=full)


def calls(stack) -> list[list[str]]:
    log = stack / "docker.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def deletes(stack) -> list[list[str]]:
    return [c for c in calls(stack) if "DELETE" in c or (c[2:3] == ["mc"] and "rb" in c)]


def namespaces(stack) -> dict:
    return json.loads((stack / "world.json").read_text())["warehouses"]["k2"]["namespaces"]


def tree(d: Path) -> dict[str, str]:
    return {str(p.relative_to(d)): p.read_text() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}


def copy_root(stack) -> Path:
    days = list((stack / "k2-backup/drop").iterdir())
    assert len(days) == 1
    return days[0]


@pytest.mark.parametrize("target, env, says", [
    (None, {}, "TARGET is empty"),
    ("", {}, "TARGET is empty"),
    ("k2", {}, "whole warehouse"),
    ("k2.spike.t1.extra", {}, "is not"),
    ("bucket:", {}, "is not"),
    ("bucket:Bad_Name", {}, "is not"),
    ("k2.spike;rm -rf ~", {}, "is not"),
    ("k2..t1", {}, "is not"),
    ("k2.spike/../gold", {}, "is not"),
    ("k2.spike", {"DRY_RUN": "yes"}, "DRY_RUN must be 0 or 1"),
])
def test_refuses_a_bad_target_before_touching_the_stack(stack, target, env, says):
    out = run(stack, target, **env)
    assert out.returncode == 2, out.stderr
    assert "REFUSED" in out.stderr and says in out.stderr
    assert calls(stack) == []


@pytest.mark.parametrize("name, loc, says", [
    ("whole warehouse", "s3://k2-lake/warehouse/k2", "not inside the warehouse's prefix"),
    ("another warehouse", "s3://k2-lake/warehouse/other/u-x", "not inside the warehouse's prefix"),
    ("another bucket", "s3://elsewhere/warehouse/k2/u-x", "not inside the warehouse's prefix"),
    ("climbs out", "s3://k2-lake/warehouse/k2/../k2-escape", "not inside the warehouse's prefix"),
    ("same as another table", LOC.format("bars"), "shared with k2.gold.bars"),
    ("inside another table", LOC.format("bars") + "/sub", "shared with k2.gold.bars"),
])
def test_refuses_a_location_outside_the_warehouse_or_shared(stack, name, loc, says):
    world = json.loads((stack / "world.json").read_text())
    world["warehouses"]["k2"]["namespaces"]["spike"]["t2"] = ["table", loc]
    (stack / "world.json").write_text(json.dumps(world))
    before = tree(stack / "minio")
    out = run(stack, "k2.spike.t2")
    assert out.returncode == 1, (name, out.stderr)
    assert says in out.stderr, name
    assert run(stack, "k2.spike").returncode == 1   # and the namespace that holds it
    assert deletes(stack) == [] and not any("mirror" in c for c in calls(stack))
    assert tree(stack / "minio") == before and "t1" in namespaces(stack)["spike"]


def test_refuses_a_bucket_that_holds_a_warehouse(stack):
    out = run(stack, "bucket:k2-lake")
    assert out.returncode == 1
    assert "holds a lake warehouse" in out.stderr
    assert deletes(stack) == [] and (stack / "minio/k2-lake").is_dir()


def test_refuses_a_table_whose_metadata_file_is_not_in_storage(stack):
    world = json.loads((stack / "world.json").read_text())
    world["warehouses"]["k2"]["namespaces"]["spike"]["t1"].append(LOC.format("t1") + "/metadata/00009-z.metadata.json")
    (stack / "world.json").write_text(json.dumps(world))
    out = run(stack, "k2.spike.t1")
    assert out.returncode == 1
    assert "current metadata file is not at" in out.stderr
    assert deletes(stack) == [] and not any("mirror" in c for c in calls(stack))


@pytest.mark.parametrize("inside", ["repo/backup", "minio-data/landing/backup"])
def test_refuses_a_backup_dir_inside_the_checkout_or_minio(stack, inside):
    out = run(stack, "k2.spike", K2_BACKUP_DIR=str(stack / inside), MINIO_MOUNT=str(stack / "minio-data"))
    assert out.returncode == 1
    assert "inside" in out.stderr
    assert deletes(stack) == [] and not (stack / inside).exists()


def test_dry_run_lists_and_changes_nothing(stack):
    world, before = (stack / "world.json").read_text(), tree(stack / "minio")
    out = run(stack, "k2.spike", DRY_RUN="1")
    assert out.returncode == 0, out.stderr
    assert f"drop: k2.spike.t1: {LOC.format('t1')}: {OBJECTS} objects, {BYTES} bytes" in out.stdout
    assert "data/p=1/a.parquet" in out.stdout and "would then drop namespace k2.spike" in out.stdout
    assert "DRY RUN: nothing copied, nothing dropped" in out.stdout
    assert (stack / "world.json").read_text() == world and tree(stack / "minio") == before
    assert not (stack / "k2-backup").exists()
    assert deletes(stack) == [] and not any(c[3:4] in (["mirror"], ["cp"]) for c in calls(stack))


def test_drops_a_namespace_after_a_matching_copy_in_order(stack):
    out = run(stack, "k2.spike")
    assert out.returncode == 0, out.stderr
    day = copy_root(stack)
    for name in ("t1", "v1"):
        assert tree(day / "k2.spike" / name) == FILES
        assert (f"drop: k2.spike.{name}: copied {LOC.format(name)} to {day}/k2.spike/{name}: {OBJECTS} objects, "
                f"{BYTES} bytes in MinIO; {OBJECTS} objects, {BYTES} bytes in the copy: match") in out.stdout
        assert not (stack / "minio" / LOC.format(name)[5:]).exists()
    assert "spike" not in namespaces(stack)
    assert tree(stack / "minio" / LOC.format("bars")[5:]) == FILES and "bars" in namespaces(stack)["gold"]
    [manifest] = day.glob("k2.spike.*.json")
    rows = json.loads(manifest.read_text())["objects"]
    assert [(r["name"], r["location"], r["objects"]) for r in rows] == [
        ("k2.spike.t1", LOC.format("t1"), OBJECTS), ("k2.spike.v1", LOC.format("v1"), OBJECTS)]
    # Order: every copy and re-listing, then the table and view drops, then the namespace.
    c = calls(stack)
    first_delete = next(i for i, a in enumerate(c) if "DELETE" in a)
    assert max(i for i, a in enumerate(c) if "mirror" in a) < first_delete
    drops = [a[-1].split("/catalog/v1/p-k2/namespaces/")[1] for a in c if "DELETE" in a]
    assert drops == ["spike/tables/t1?purgeRequested=true", "spike/views/v1?purgeRequested=true", "spike"]


@pytest.mark.parametrize("switch", ["SKIP", "TRUNCATE"])
def test_a_count_or_bytes_mismatch_stops_before_the_drop(stack, switch):
    world, before = (stack / "world.json").read_text(), tree(stack / "minio")
    out = run(stack, "k2.spike.t1", **{switch: "1"})
    assert out.returncode == 1
    assert "in the copy" in out.stderr and "nothing was dropped" in out.stderr
    assert deletes(stack) == []
    assert (stack / "world.json").read_text() == world and tree(stack / "minio") == before


def test_an_earlier_copy_that_differs_is_never_overwritten(stack):
    earlier = stack / "k2-backup/drop" / subprocess.run(["date", "-u", "+%F"], capture_output=True, text=True).stdout.strip()
    f = earlier / "k2.spike.t1/data/p=1/a.parquet"
    f.parent.mkdir(parents=True)
    f.write_text("an earlier copy")
    out = run(stack, "k2.spike.t1")
    assert out.returncode == 1
    assert "1 object(s) not copied" in out.stderr and "nothing was overwritten or dropped" in out.stderr
    assert f.read_text() == "an earlier copy"
    assert deletes(stack) == [] and "t1" in namespaces(stack)["spike"]


def test_a_write_during_the_copy_stops_before_the_drop(stack):
    out = run(stack, "k2.spike.t1", GROW="1")
    assert out.returncode == 1
    assert "changed while it was copied" in out.stderr
    assert deletes(stack) == [] and "t1" in namespaces(stack)["spike"]


def test_too_little_room_stops_before_the_copy(stack):
    out = run(stack, "k2.spike", K2_BACKUP_MIN_FREE_GIB=str(10**9))
    assert out.returncode == 1
    assert "under the floor" in out.stderr
    assert deletes(stack) == [] and not any("mirror" in c for c in calls(stack))
    assert tree(stack / "k2-backup") == {}


def test_a_purge_that_leaves_files_fails(stack):
    out = run(stack, "k2.spike.t1", NO_PURGE="1")
    assert out.returncode == 1
    assert f"{OBJECTS} objects are still at {LOC.format('t1')}" in out.stderr
    assert tree(copy_root(stack) / "k2.spike.t1") == FILES


def test_drops_a_versioned_bucket_with_every_version_copied(stack):
    out = run(stack, "bucket:landing")
    assert out.returncode == 0, out.stderr
    day = copy_root(stack)
    assert tree(day / "bucket:landing") == {"a.txt": "new a"}
    assert tree(day / "bucket:landing.versions") == {"a.txt/va1": "old a", "a.txt/va2": "new a", "gone.txt/vg1": "gone body"}
    assert "1 objects, 5 bytes in MinIO; 1 objects, 5 bytes in the copy; 3 object versions, 19 bytes" in out.stdout
    assert not (stack / "minio/landing").exists()
    c = calls(stack)
    rb = next(i for i, a in enumerate(c) if a[2:4] == ["mc", "rb"])
    assert max(i for i, a in enumerate(c) if a[3:4] in (["mirror"], ["cp"])) < rb


@pytest.mark.parametrize("target, env, rc", [
    ("k2.spike", {}, 0),
    ("k2.spike", {"DRY_RUN": "1"}, 0),
    ("k2.spike.t1", {"TRUNCATE": "1"}, 1),
    ("bucket:landing", {}, 0),
])
def test_never_prints_or_passes_the_credential(stack, target, env, rc):
    out = run(stack, target, **env)
    assert out.returncode == rc, out.stderr
    written = "".join(p.read_text() for p in (stack / "k2-backup").rglob("*.json")) if (stack / "k2-backup").exists() else ""
    for secret in (USER, PASSWORD):
        assert secret not in out.stdout + out.stderr
        assert secret not in (stack / "docker.log").read_text(), "on a docker command line"
        assert secret not in written
