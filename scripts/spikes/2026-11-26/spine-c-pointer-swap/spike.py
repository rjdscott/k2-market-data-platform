"""N0-4 (c): the release pointer swap through a read-only directory mount on this Docker Desktop host.

A releases root laid out as IMPLEMENTATION section A1 (`<id>/` beside `pointers/<service> -> ../<id>`)
is mounted read-only, as a directory, at /k2/releases into a throwaway Trino 484 (file rules and file
group provider read through `pointers/trino`, 5 s refresh each) and a throwaway Airflow 3.3.2
dag-processor (SQLite metadata DB in the container, DAGS_FOLDER through `pointers/airflow`). The
pointers are swapped with `ln -s` + `mv -T` in three ways, SWAPS times each:

  host          on the host, nothing else
  host+readdir  on the host, then one `ls /k2/releases/pointers` in a helper container
  in-vm         inside a helper container that mounts the root read-write (Docker Desktop's VM side)

and both containers are polled, by path lookups only (never a listing of `pointers/`), until they
apply the new release. After each swap a listing in the helper settles both containers before the
next. Then: in-flight reads across a swap, a pointer mounted directly, an absolute link target, and
a mounted directory deleted and recreated. Synthetic files only; no live service is touched.

    SWAPS=5 HOST_TIMEOUT=120 AIRFLOW_REFRESH_INTERVAL=30 python3 spike.py
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_lib import (  # noqa: E402
    AIRFLOW_IMAGE,
    ALPINE_IMAGE,
    LABEL,
    Trino,
    docker,
    log,
    remove_containers,
    wait_memory,
    write_trino_etc,
)

SWAPS = int(os.environ.get("SWAPS", "5"))
HOST_TIMEOUT = int(os.environ.get("HOST_TIMEOUT", "120"))
REFRESH = int(os.environ.get("AIRFLOW_REFRESH_INTERVAL", "30"))  # the live stack runs Airflow's default, 300
MIN_FILE = int(os.environ.get("AIRFLOW_MIN_FILE_PROCESS_INTERVAL", "30"))  # Airflow's default
TRINO_REFRESH = "5s"  # N1-21's value for security.refresh-period and file.refresh-period
IDS = ["v0.0.0-spike.1-aaaaaaaaaaaa", "v0.0.0-spike.2-bbbbbbbbbbbb"]
DAGS = "platform/docker/airflow/dags"
MNT = "/k2/releases"
MODES = ["host", "host+readdir", "in-vm"]
TC, AC, HC, PC, SC = (f"spike-n04plat-c-{n}" for n in ("trino", "airflow", "helper", "pinned", "stale"))

BASE = Path(tempfile.mkdtemp(prefix="spike-n04plat-c.", dir=Path.home()))  # Docker Desktop shares $HOME
ROOT = BASE / "k2-releases"


def rules(n: int) -> str:
    return json.dumps({"catalogs": [
        {"group": "g_tpch", "catalog": "tpch", "allow": "read-only"},
        {"group": "g_memory", "catalog": "memory", "allow": "read-only"},
        {"user": "u_rules", "catalog": "tpch" if n == 1 else "memory", "allow": "read-only"},
        {"catalog": "system", "allow": "read-only"},
    ]}, indent=1)


DAG = '''from datetime import datetime

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG

with DAG(dag_id="{dag_id}", description="{rid}", schedule=None, start_date=datetime(2026, 1, 1)):
    EmptyOperator(task_id="t")
'''


def build_release(n: int) -> str:
    """Build into .<id>.tmp, seal read-only, rename into place (A1). Returns big.bin's sha256."""
    rid = IDS[n - 1]
    tmp = ROOT / f".{rid}.tmp"
    (tmp / "compiled/trino").mkdir(parents=True)
    (tmp / DAGS).mkdir(parents=True)
    (tmp / "marker").write_text(rid + "\n")
    (tmp / "compiled/trino/rules.json").write_text(rules(n))
    (tmp / "compiled/trino/groups.txt").write_text(("g_tpch" if n == 1 else "g_memory") + ":u_group\n")
    (tmp / DAGS / "common.py").write_text(DAG.format(dag_id="spike_common", rid=rid))
    (tmp / DAGS / f"only_{n}.py").write_text(DAG.format(dag_id=f"spike_only_{n}", rid=rid))
    big = os.urandom(16 << 20)
    (tmp / "big.bin").write_bytes(big)
    for p in sorted(tmp.rglob("*"), reverse=True) + [tmp]:
        p.chmod(0o555 if p.is_dir() else 0o444)
    tmp.rename(ROOT / rid)
    return hashlib.sha256(big).hexdigest()


# A fresh temporary name per swap: after a host-side rename the VM side can still resolve the old
# temporary name (finding (c)), and `ln -s` onto a name that resolves to a directory links inside it.
SWAP_SH = ('for s in {services}; do ln -s "../{rid}" "pointers/.$s.{uniq}" && '
           'mv -T "pointers/.$s.{uniq}" "pointers/$s"; done')
_swaps = itertools.count()


def swap(mode: str, rid: str, services: str = "trino airflow") -> float:
    """The deploy's pointer swap: a new relative link, then an atomic rename over the old one."""
    t0 = time.monotonic()
    sh = SWAP_SH.format(services=services, rid=rid, uniq=f"{os.getpid()}-{next(_swaps)}")
    if mode == "in-vm":
        docker("exec", HC, "sh", "-c", f"cd {MNT} && {sh}")
    else:
        subprocess.run(["sh", "-c", sh], cwd=ROOT, check=True)
        if mode == "host+readdir":
            docker("exec", HC, "ls", f"{MNT}/pointers")
    return t0


def trino_release(t: Trino, user: str) -> int | None:
    rows, err = t.q(user, "SHOW CATALOGS")
    names = {r[0] for r in rows}
    return None if err else 1 if "tpch" in names else 2 if "memory" in names else 0


AIRFLOW_POLL = """
import json, os, sqlite3
p = "/k2/releases/pointers/airflow"
out = {"link": os.readlink(p)}
try:
    out["marker"] = open(p + "/marker").read().strip()
except OSError as e:
    out["marker"] = repr(e)
try:
    c = sqlite3.connect("file:/tmp/airflow.db?mode=ro", uri=True, timeout=2)
    out["dags"] = {r[0]: [r[1], r[2], r[3]] for r in c.execute("select dag_id, is_stale, description, fileloc from dag")}
    out["import_errors"] = c.execute("select count(*) from import_error").fetchone()[0]
except Exception as e:
    out["dags"] = {}
    out["db_error"] = repr(e)[:200]
print(json.dumps(out))
"""


def airflow_state() -> dict:
    return json.loads(docker("exec", AC, "python3", "-c", AIRFLOW_POLL))


def poll(name: str, probe, want: dict, timeout: float, t0: float, out: dict,
         gate: tuple[str, float] | None = None) -> None:
    """Call probe() until every key of `want` has matched once; record seconds since t0 per key.
    `gate` = (key, s): stop early if that key has not matched by then."""
    first: dict[str, float] = {}
    flaps, errors, loop_t = 0, 0, []
    while len(first) < len(want) and time.monotonic() - t0 < timeout:
        if gate and gate[0] not in first and time.monotonic() - t0 > gate[1]:
            break
        s = time.monotonic()
        try:
            got = probe()
        except Exception:
            errors += 1
            got = {}
        now = time.monotonic() - t0
        for k, v in want.items():
            hit = got.get(k) == v
            if hit and k not in first:
                first[k] = now
            elif not hit and k in first:
                flaps += 1
        loop_t.append(time.monotonic() - s)
        time.sleep(0.2)
    out[name] = {"first": first, "missing": sorted(set(want) - set(first)), "flaps": flaps, "errors": errors,
                 "poll_s": statistics.median(loop_t) + 0.2 if loop_t else None}


def main() -> None:
    wait_memory()
    ROOT.mkdir(mode=0o755)
    (ROOT / "pointers").mkdir(mode=0o755)
    shas = {IDS[0]: build_release(1)}
    for svc in ("trino", "airflow"):
        os.symlink(f"../{IDS[0]}", ROOT / "pointers" / svc)
    ptr = f"{MNT}/pointers/trino/compiled/trino"
    etc = BASE / "trino-etc"
    write_trino_etc(etc, {"tpch": "connector.name=tpch\n", "memory": "connector.name=memory\n"}, extra_files={
        "access-control.properties": f"access-control.name=file\nsecurity.config-file={ptr}/rules.json\n"
                                     f"security.refresh-period={TRINO_REFRESH}\n",
        "group-provider.properties": f"group-provider.name=file\nfile.group-file={ptr}/groups.txt\n"
                                     f"file.refresh-period={TRINO_REFRESH}\n"})
    mount = f"{ROOT}:{MNT}:ro"
    try:
        trino = Trino(TC, etc, network="bridge", mounts=(mount,))
        log(f"trino up in {trino.start_s:.0f} s")
        remove_containers(AC, HC)
        docker("run", "-d", "--name", AC, "--label", LABEL, "--network", "none", "--memory", "1g", "--cpus", "1",
               "-v", mount,
               "-e", "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////tmp/airflow.db",
               "-e", f"AIRFLOW__CORE__DAGS_FOLDER={MNT}/pointers/airflow/{DAGS}",
               "-e", "AIRFLOW__CORE__LOAD_EXAMPLES=False",
               "-e", f"AIRFLOW__DAG_PROCESSOR__REFRESH_INTERVAL={REFRESH}",
               "-e", f"AIRFLOW__DAG_PROCESSOR__MIN_FILE_PROCESS_INTERVAL={MIN_FILE}",
               "--entrypoint", "bash", AIRFLOW_IMAGE,
               "-c", "airflow db migrate > /tmp/migrate.log 2>&1 && exec airflow dag-processor")
        # The helper stands in for a deploy step that runs on the VM side: the root, read-write.
        docker("run", "-d", "--name", HC, "--label", LABEL, "--network", "none", "--memory", "32m",
               "-v", f"{ROOT}:{MNT}", ALPINE_IMAGE, "sleep", "7200")
        # Release 2 appears in the mounted root only after every container started.
        shas[IDS[1]] = build_release(2)
        base: dict = {}
        poll("airflow", lambda: {"common": airflow_state()["dags"].get("spike_common", [None, None])[1]},
             {"common": IDS[0]}, 300, time.monotonic(), base)
        if base["airflow"]["missing"]:
            raise RuntimeError("dag-processor never parsed release 1: " + docker("logs", "--tail", "40", AC, check=False))
        log(f"baseline: dag-processor parsed release 1; trino rules {trino_release(trino, 'u_rules')}, "
            f"groups {trino_release(trino, 'u_group')}")

        def trino_probe() -> dict:
            ln = docker("exec", TC, "bash", "-c", f"readlink {MNT}/pointers/trino; cat {MNT}/pointers/trino/marker")
            link, marker = (ln.splitlines() + ["", ""])[:2]
            return {"link": link, "marker": marker, "rules": trino_release(trino, "u_rules"),
                    "groups": trino_release(trino, "u_group")}

        def airflow_probe(new: int, old: int) -> dict:
            s = airflow_state()
            d = s["dags"]
            return {"link": s["link"], "marker": s["marker"], "common": d.get("spike_common", [None, None])[1],
                    "new_active": d.get(f"spike_only_{new}", [1])[0] in (0, False),
                    "old_stale": d.get(f"spike_only_{old}", [0])[0] in (1, True)}

        def measure(new: int, old: int, t0: float, gate: float | None) -> dict:
            rid = IDS[new - 1]
            res: dict = {}
            g = ("link", gate) if gate else None
            th = [threading.Thread(daemon=True, target=poll, args=(
                      "trino", trino_probe, {"link": f"../{rid}", "marker": rid, "rules": new, "groups": new},
                      90, t0, res, g)),
                  threading.Thread(daemon=True, target=poll, args=(
                      "airflow", lambda: airflow_probe(new, old), {"link": f"../{rid}", "marker": rid, "common": rid,
                                                                    "new_active": True, "old_stale": True},
                      REFRESH + 150, t0, res, g))]
            for t in th:
                t.start()
            for t in th:
                t.join()
            return res

        swaps = []
        cur = 1
        for mode in MODES:
            for i in range(SWAPS):
                new = 2 if cur == 1 else 1
                t0 = swap(mode, IDS[new - 1])
                res = measure(new, cur, t0, HOST_TIMEOUT if mode == "host" else None)
                swaps.append({"mode": mode, **res})
                log(f"{mode} swap {i + 1}/{SWAPS} -> {IDS[new - 1][-14:]}:",
                    json.dumps({k: {kk: round(vv, 1) for kk, vv in v["first"].items()}
                                | ({"missing": v["missing"]} if v["missing"] else {}) for k, v in res.items()}))
                if res["trino"]["missing"] or res["airflow"]["missing"]:  # settle before the next swap
                    t1 = time.monotonic()
                    docker("exec", HC, "ls", f"{MNT}/pointers")
                    settle = measure(new, cur, t1, None)
                    log("  settled after a listing in the helper:",
                        json.dumps({k: v["missing"] or "all" for k, v in settle.items()}))
                cur = new

        # In-flight reads: an open file, a directory listing taken before the swap, and a path
        # resolved before the swap, each read after it.
        inflight = r'''
import hashlib, json, os, sys
p = "/k2/releases/pointers/airflow"
d = "/platform/docker/airflow/dags"
resolved = os.path.realpath(p)
f = open(p + "/big.bin", "rb")
head = f.read(8 << 20)
listing = sorted(os.listdir(p + d))
print("opened", flush=True)
sys.stdin.readline()
tail = f.read()
def desc(base, n):
    try:
        return open(base + d + "/" + n).read().split('description="')[1].split('"')[0][-14:]
    except FileNotFoundError:
        return "ENOENT"
print(json.dumps({"fd_sha": hashlib.sha256(head + tail).hexdigest(), "before": resolved,
                  "after": os.path.realpath(p), "listing": listing,
                  "via_pointer": {n: desc(p, n) for n in listing},
                  "via_resolved": {n: desc(resolved, n) for n in listing}}))
'''
        proc = subprocess.Popen(["docker", "exec", "-i", AC, "python3", "-c", inflight], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True)
        assert proc.stdout and proc.stdout.readline().strip() == "opened"
        new = 2 if cur == 1 else 1
        swap("in-vm", IDS[new - 1])
        out, _ = proc.communicate("go\n", timeout=60)
        fl = json.loads(out)
        fl["fd_is_old_release"] = fl["fd_sha"] == shas[IDS[cur - 1]]
        cur = new
        log("in-flight:", json.dumps(fl))

        # A pointer mounted directly, and an absolute link target, beside the root mount.
        remove_containers(PC)
        docker("run", "-d", "--name", PC, "--label", LABEL, "--network", "none", "--memory", "32m",
               "-v", f"{ROOT}/pointers/trino:/pinned:ro", "-v", mount, ALPINE_IMAGE, "sleep", "600")
        docker("exec", HC, "ln", "-s", str(ROOT / IDS[0]), f"{MNT}/pointers/absolute")
        sh = f"cat /pinned/marker; cat {MNT}/pointers/trino/marker; readlink {MNT}/pointers/absolute; " \
             f"cat {MNT}/pointers/absolute/marker 2>&1 || true"
        before = docker("exec", PC, "sh", "-c", sh).split("\n")
        new = 2 if cur == 1 else 1
        swap("in-vm", IDS[new - 1], services="trino")
        time.sleep(3)
        after = docker("exec", PC, "sh", "-c", sh).split("\n")
        pinned = {"pinned_before": before[0], "root_before": before[1], "pinned_after": after[0],
                  "root_after": after[1], "absolute_link": after[2], "absolute_read": after[3]}
        log("pinned and absolute:", json.dumps(pinned))

        # A mounted directory deleted and recreated on the host (CLAUDE.md, bind-mount gotcha).
        stale_root = BASE / "stale-root"
        stale_root.mkdir()
        (stale_root / "marker").write_text("one\n")
        remove_containers(SC)
        docker("run", "-d", "--name", SC, "--label", LABEL, "--network", "none", "--memory", "32m",
               "-v", f"{stale_root}:/r:ro", ALPINE_IMAGE, "sleep", "600")
        rd = "cat /r/marker 2>&1; ls -a /r 2>&1 | tr '\\n' ' '"
        stale = {"before": docker("exec", SC, "sh", "-c", rd).strip()}
        shutil.rmtree(stale_root)
        stale_root.mkdir()
        (stale_root / "marker").write_text("two\n")
        t_del = time.monotonic()
        for wait in (1, 10, 30):
            time.sleep(max(0.0, wait - (time.monotonic() - t_del)))
            stale[f"after_{wait}s"] = docker("exec", SC, "sh", "-c", rd).strip().replace("\n", " | ")
        log("deleted and recreated mount source:", json.dumps(stale))

        p = subprocess.run(["docker", "logs", TC], capture_output=True, text=True)
        trino_errors = [ln for ln in (p.stdout + p.stderr).splitlines() if "ERROR" in ln or "Exception" in ln]
        report(swaps, fl, pinned, stale, trino_errors, airflow_state())
    finally:
        remove_containers(TC, AC, HC, PC, SC)
        for p in [BASE, *BASE.rglob("*")]:
            if p.is_dir() and not p.is_symlink():
                p.chmod(0o755)
        shutil.rmtree(BASE)
        left = docker("ps", "-a", "--filter", "name=spike-n04plat-c-", "--format", "{{.Names}}").split()
        log(f"cleanup: containers left {left}, temp dir removed {not BASE.exists()}")


def stats(xs: list[float], n: int) -> str:
    if not xs:
        return f"never, n={n}"
    tail = f", {n - len(xs)} of {n} never" if len(xs) < n else ""
    return f"n={len(xs)} min {min(xs):.1f} / median {statistics.median(xs):.1f} / max {max(xs):.1f} s{tail}"


def report(swaps, fl, pinned, stale, trino_errors, final) -> None:
    log("")
    log(f"FINDING (c): pointer swap through a read-only directory mount of the releases root, Docker Desktop, "
        f"{SWAPS} swaps per mode; seconds from the swap")
    for mode in MODES:
        ss = [s for s in swaps if s["mode"] == mode]

        def col(svc: str, key: str, ss: list = ss) -> list[float]:
            return [s[svc]["first"][key] for s in ss if key in s[svc]["first"]]

        n = len(ss)
        cut = f" (not seen within {HOST_TIMEOUT} s counts as never)" if mode == "host" else ""
        log(f"  [{mode}]{cut}")
        log(f"    Trino: new link seen {stats(col('trino', 'link'), n)}")
        log(f"    Trino: rules applied (refresh {TRINO_REFRESH}) {stats(col('trino', 'rules'), n)}")
        log(f"    Trino: groups applied (refresh {TRINO_REFRESH}) {stats(col('trino', 'groups'), n)}")
        mixed = [abs(s["trino"]["first"]["rules"] - s["trino"]["first"]["groups"]) for s in ss
                 if {"rules", "groups"} <= set(s["trino"]["first"])]
        log(f"    Trino: rules and groups from different releases for {stats(mixed, n)}")
        log(f"    Airflow: new link seen {stats(col('airflow', 'link'), n)}")
        log(f"    Airflow: changed DAG file reparsed (min_file_process_interval {MIN_FILE}) "
            f"{stats(col('airflow', 'common'), n)}")
        log(f"    Airflow: new DAG file active (refresh_interval {REFRESH}) {stats(col('airflow', 'new_active'), n)}")
        log(f"    Airflow: removed DAG file marked stale (refresh_interval {REFRESH}) "
            f"{stats(col('airflow', 'old_stale'), n)}")
    flaps = sum(s[v]["flaps"] for s in swaps for v in ("trino", "airflow"))
    errs = sum(s[v]["errors"] for s in swaps for v in ("trino", "airflow"))
    polls = [s[v]["poll_s"] for s in swaps for v in ("trino", "airflow") if s[v]["poll_s"]]
    log(f"  flaps back to the old release: {flaps}; probe errors: {errs}; median poll period "
        f"{statistics.median(polls):.2f} s (the resolution of every number above)")
    log(f"  Trino log lines with ERROR or Exception: {len(trino_errors)}"
        + (f"; first: {trino_errors[0][:200]}" if trino_errors else ""))
    log(f"  Airflow fileloc of spike_common: {final['dags'].get('spike_common', [None, None, None])[2]}; "
        f"import errors {final.get('import_errors')}")
    log(f"  in-flight: an fd opened before the swap read the old release's file to the end: {fl['fd_is_old_release']}")
    log(f"  in-flight: names listed before the swap, opened through the pointer after it: {fl['via_pointer']}")
    log(f"  in-flight: the same names opened through the path resolved before the swap:    {fl['via_resolved']}")
    log(f"  pointer mounted directly (-v .../pointers/trino:/pinned): before {pinned['pinned_before'][-14:]}, "
        f"after a swap {pinned['pinned_after'][-14:]} while the root mount shows {pinned['root_after'][-14:]}")
    log(f"  absolute link target {pinned['absolute_link']!r} read in a container: {pinned['absolute_read']!r}")
    log(f"  mounted directory deleted and recreated on the host: {stale}")


if __name__ == "__main__":
    main()
