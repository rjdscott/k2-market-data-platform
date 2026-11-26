"""spine-h-tested-data-view: the gate of phase 001 step S01 (decision O8, design (a) as amended).

Design under test: a dbt output is read only through a definer view `<fence>.<table>`, compiled as
`SELECT <contract columns> FROM <reader catalog>.<fence>__base.<table> FOR VERSION AS OF 'audited'`,
owned by the publisher `pub_<fence>`, the only principal with GRANT_SELECT on that base; reader
policies are Trino rules on the view's exact name; reader catalogs run read-only with table
statistics off; the publisher moves the `audited` tags in one multi-table REST commit; the catalog
API is fenced by a network, with a drift check behind it.

  h1  the fence matrix on a throwaway Trino 484 (file rules, file groups) over scratch warehouse
      `spike_s01` in the live Lakekeeper: products A, B (and a test-only publisher X), readers with
      a full and a delayed role, an unrelated user.
  h2  the polled cycle: product C, two outputs, 23 publishes, a failing build, an added column, a
      dropped column, a kill between the tag commit and the view update, and a reader polling both
      views about every 50 ms throughout.
  h3  the catalog fence: a throwaway Lakekeeper with its own Postgres and MinIO on network
      `spike-s01-catalog`, forged from k2-net (unreachable) and from inside (accepted), with the
      drift check run against it.

Every container is `spike-s01-*`, labelled `k2.spike=s01`, and removed, as are the scratch
warehouse, its bucket `spike-s01` and the network, pass or fail. Synthetic rows only. The MinIO
root key comes from the main checkout's `.env` into this process and reaches containers by
variable name only; the throwaway stack's keys are random per run. Exit 0 only when every
assertion held; 1 when one failed or the run did not complete.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # platform_lib.py, the 2026-11-26 platform spikes' helpers
sys.path.insert(0, str(HERE))
import catalog as rc  # noqa: E402
import platform_lib as pl  # noqa: E402

pl.LABEL = "k2.spike=s01"
P = "spike-s01"
NET_FENCE = "spike-s01-catalog"
# The image docker/spark/Dockerfile builds from, for its python3: the in-network client (tools) and the k2-net probe.
TOOLS_IMAGE = ("spark:3.5.9-scala2.12-java17-python3-ubuntu"
               "@sha256:8901b8427694267ed719f27aa1498815dbd3573efb3cde447246fbff29d07272")
LK_LIVE = "http://127.0.0.1:18181"          # the live Lakekeeper's published port (it moves off the host in S22)
WH, BUCKET = "spike_s01", "spike-s01"        # the live scratch warehouse and its scratch bucket
FL = f"http://{P}-lakekeeper:8181"           # the throwaway Lakekeeper, reachable only on NET_FENCE
READER, BUILD = "s01|fence", "s01_build|fence_build"
SVC, FULL, DELAYED, OTHER = "svc.platform", "p.reader-full", "p.reader-delayed", "p.unrelated"
A, AB, B, BB, X, C, CB = ("spike_s01_a", "spike_s01_a__base", "spike_s01_b", "spike_s01_b__base", "spike_s01_x",
                          "spike_s01_c", "spike_s01_c__base")
WRITE = ["SELECT", "INSERT", "DELETE", "UPDATE", "OWNERSHIP"]  # a builder's: no GRANT_SELECT
COLS = ["id", "region", "secret", "ts", "amount"]
DDL = "id bigint, region varchar, secret varchar, ts timestamp(6) with time zone, amount bigint"
OLD, NOW = "current_timestamp - INTERVAL '2' HOUR", "current_timestamp"
A_TESTED = [(1, "open", "zz-sec-1", OLD, 10), (2, "open", "zz-sec-2", OLD, 20),
            (3, "restricted", "zz-sec-3", OLD, 333333), (5, "open", "zz-sec-5", NOW, 555555)]
A_UNTESTED = [(4, "open", "zz-sec-4", OLD, 444444)]
B_TESTED = [(101, "open", "b-sec-101", OLD, 1), (102, "open", "b-sec-102", OLD, 2)]
# Values a reader must never see anywhere (rows, statistics, plans): hidden by policy or untested.
HIDDEN = {FULL: ["zz-sec-3", "zz-sec-4", "333333", "444444"],
          DELAYED: ["zz-sec-", "333333", "444444", "555555"]}
SCOPE = "region <> 'restricted'"
DELAY = f"{SCOPE} AND ts < current_timestamp - INTERVAL '20' MINUTE"

PORT = 0
BASE = Path()
LIVE: rc.Catalog
RESULTS: list[tuple[str, str, str, str]] = []
POLLER: Poller | None = None


# ---- rules: conditions 1 to 3 of the proposal, generated per product --------------------------------

def product_rules(fence: str, roles: dict, inputs: tuple = (), builder: bool = True) -> tuple[list, list]:
    """Builder dbt_<p> owns <fence>__base in the writer catalogs, no GRANT_SELECT; publisher pub_<p>
    owns <fence> there (views only) and holds SELECT, GRANT_SELECT on its base through the reader
    catalogs; each reader role is one rule on exact view names, carrying its policy."""
    base, p = f"{fence}__base", fence.removeprefix("spike_s01_")
    dbt, pub = f"dbt_{p}", f"pub_{p}"
    schemas = [{"user": pub, "catalog": BUILD, "schema": fence, "owner": True}]
    tables = [{"user": pub, "catalog": BUILD, "schema": fence, "privileges": ["SELECT", "OWNERSHIP"]},
              {"user": pub, "catalog": READER, "schema": base, "privileges": ["SELECT", "GRANT_SELECT"]},
              {"user": pub, "catalog": READER, "schema": fence, "privileges": ["SELECT"]}]
    if builder:
        schemas.insert(0, {"user": dbt, "catalog": BUILD, "schema": base, "owner": True})
        tables.append({"user": dbt, "catalog": BUILD, "schema": base, "privileges": WRITE})
        tables += [{"user": dbt, "catalog": READER, "schema": f"{f}__base", "table": t, "privileges": ["SELECT"]}
                   for f, t in inputs]
    for role, (views, filt, masks) in roles.items():
        rule = {"group": f"{fence}__{role}", "catalog": READER, "schema": fence, "table": views,
                "privileges": ["SELECT"]}
        if filt:
            rule["filter"] = filt
        if masks:
            rule["columns"] = [{"name": c, "mask": m} for c, m in masks.items()]
        tables.append(rule)
    return schemas, tables


def rules() -> dict:
    parts = [product_rules(A, {"full_reader": ("t|inv", SCOPE, None),
                               "delayed_reader": ("t|inv", DELAY, {"secret": "CAST(NULL AS varchar)"})}),
             product_rules(B, {"full_reader": ("t|leak", None, None)}, inputs=((A, "t"),)),
             product_rules(X, {"full_reader": ("v", None, None)}, builder=False),
             product_rules(C, {"full_reader": ("t1|t2", None, None)})]
    # Test only: X's publisher may SELECT A's base but not GRANT_SELECT it, so h1.14 tests that boundary alone.
    x_select = {"user": "pub_x", "catalog": READER, "schema": AB, "table": "t", "privileges": ["SELECT"]}
    return {
        # A definer view's owner needs catalog access ALL where its base lives (FileBasedSystemAccessControl
        # .checkCanCreateViewWithSelectFromColumns), so publishers get `all` on the reader catalogs; the
        # connector's iceberg.security=read_only refuses their writes there (h1.02).
        "catalogs": [{"user": "svc\\.platform|dbt_.*|pub_.*", "catalog": BUILD, "allow": "all"},
                     {"user": "pub_.*", "catalog": READER, "allow": "all"},
                     {"catalog": READER, "allow": "read-only"},
                     {"catalog": "system", "allow": "read-only"}],
        "schemas": [{"user": "svc\\.platform", "catalog": BUILD, "owner": True},
                    *[s for sc, _ in parts for s in sc], {"owner": False}],
        "tables": [{"user": "svc\\.platform", "catalog": BUILD, "privileges": WRITE},
                   {"user": "svc\\.platform", "catalog": "system", "privileges": ["SELECT"]},
                   *[t for _, tb in parts for t in tb], x_select],
        "catalog_session_properties": [
            {"user": "svc\\.platform", "allow": True},
            {"user": "dbt_.*|pub_.*|svc\\.(runs|catalog)", "property": "statistics_enabled", "allow": True},
            {"property": "statistics_enabled|expire_snapshots_min_retention|remove_orphan_files_min_retention",
             "allow": False},
            {"allow": True}],
        "queries": [{"user": "svc\\.platform", "allow": ["execute", "kill", "view"]}, {"allow": ["execute"]}],
        "system_information": [{"allow": ["read"]}],
    }


GROUPS = "\n".join([f"{A}__full_reader:{FULL}", f"{A}__delayed_reader:{DELAYED}", f"{B}__full_reader:{FULL}",
                    f"{X}__full_reader:{FULL}", f"{C}__full_reader:{FULL}"]) + "\n"


# ---- small helpers ---------------------------------------------------------------------------------------

def record(rid: str, assertion: str, ok: bool | None, detail: object) -> None:
    """ok None: recorded for the note, not a pass criterion."""
    verdict = "INFO" if ok is None else "PASS" if ok else "FAIL"
    detail = pl.redact(detail if isinstance(detail, str) else json.dumps(detail, default=str))
    RESULTS.append((rid, verdict, assertion, detail))
    pl.log(f"{verdict} {rid} {assertion}\n      {detail}")


def tq(user: str, sql: str, session: str | None = None) -> tuple[list, str | None, dict]:
    """One statement over Trino's REST protocol: (rows, error or None, final query stats)."""
    headers = {"X-Trino-User": user}
    if session:
        headers["X-Trino-Session"] = session
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/statement", data=sql.encode(), headers=headers)
    rows: list = []
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            doc = json.load(r)
        deadline = time.monotonic() + 300
        while True:
            rows += doc.get("data", [])
            if "error" in doc:
                e = doc["error"]
                return rows, pl.redact(f"{e.get('errorName')}: {e.get('message')}"), doc.get("stats", {})
            if not doc.get("nextUri"):
                return rows, None, doc.get("stats", {})
            if time.monotonic() > deadline:
                return rows, "client timeout", {}
            with urllib.request.urlopen(doc["nextUri"], timeout=60) as r:
                doc = json.load(r)
    except urllib.error.HTTPError as e:
        return rows, f"HTTP {e.code}", {}


def ok(user: str, sql: str, session: str | None = None) -> list:
    rows, err, _ = tq(user, sql, session)
    if err:
        raise RuntimeError(f"{user}: {sql[:160]}: {err}")
    return rows


def denied(err: str | None) -> bool:
    return bool(err) and "Access Denied" in err


def short(err: str | None) -> str:
    return (err or "ok").replace("PERMISSION_DENIED: Access Denied: ", "denied: ")[:150]


def insert(table: str, rows: list) -> str:
    return f"INSERT INTO {table} VALUES " + ", ".join(f"({i}, '{r}', '{s}', {ts}, {a})" for i, r, s, ts, a in rows)


def view_sql(cat: str, fence: str, table: str, cols: list[str]) -> str:
    """Condition 2: the projection of the contract's columns over the tag, through the reader catalog."""
    return f"SELECT {', '.join(cols)} FROM {cat}.{fence}__base.{table} FOR VERSION AS OF 'audited'"


def publish_view(cat: str, fence: str, table: str, cols: list[str]) -> None:
    """The publisher's route: through the writer catalog entry, into the reader namespace."""
    ok(f"pub_{fence.removeprefix('spike_s01_')}",
       f"CREATE OR REPLACE VIEW {cat}_build.{fence}.{table} SECURITY DEFINER AS {view_sql(cat, fence, table, cols)}")


def main_id(cat: str, ns: str, table: str, user: str) -> int:
    return ok(user, f"SELECT snapshot_id FROM {cat}.{ns}.\"{table}$refs\" WHERE name = 'main'")[0][0]


def stats_bounds(rows: list) -> list:
    """SHOW STATS rows: column, size, distinct, nulls fraction, row count, low, high. Any bound or count."""
    return [r for r in rows if r[5] is not None or r[6] is not None or (r[0] is None and r[4] is not None)]


def stats_left(rows: list) -> list:
    names = ["size", "distinct", "nulls", "rows", "low", "high"]
    return [f"{r[0] or '(table)'}: " + ", ".join(f"{n} {v}" for n, v in zip(names, r[1:]) if v is not None)
            for r in rows if any(v is not None for v in r[1:])]


def docker_env(env: dict, *args: str) -> str:
    """docker with extra variables in its own environment only, passed on by name with -e NAME."""
    p = subprocess.run(["docker", *args], capture_output=True, text=True, env={**os.environ, **env})
    if p.returncode:
        raise RuntimeError(pl.redact(f"docker {args[:3]}: {p.stderr.strip()[-600:]}"))
    return p.stdout


def mc_live(script: str) -> str:
    """mc on k2-net against the live MinIO; the root key reaches mc through MC_HOST_root only."""
    user, pw = (urllib.parse.quote(os.environ[k], safe="") for k in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"))
    os.environ["MC_HOST_root"] = pl.secret(f"http://{user}:{pw}@minio:9000")
    return pl.docker("run", "--rm", "-i", "--name", f"{P}-mc", "--label", pl.LABEL, "--network", pl.NET,
                     "--memory", "128m", "-e", "MC_HOST_root", "--entrypoint", "sh", pl.MINIO_IMAGE, "-c", script)


def tools(*args: str, env_names: tuple = ()) -> tuple[int, str]:
    """catalog.py inside the throwaway catalog network (the `tools` one-shot's place in A13)."""
    env = [a for n in env_names for a in ("-e", n)]
    p = subprocess.run(["docker", "exec", *env, f"{P}-tools", "python3", "-B", "/spike/catalog.py", *args],
                       capture_output=True, text=True)
    return p.returncode, pl.redact((p.stdout + p.stderr).strip())


def from_k2net(*args: str) -> tuple[int, str]:
    """catalog.py in a fresh container on k2-net, where Grafana, the console, cadvisor and statsd live."""
    p = subprocess.run(["docker", "run", "--rm", "--name", f"{P}-probe", "--label", pl.LABEL, "--network", pl.NET,
                        "--memory", "128m", "-v", f"{HERE}:/spike:ro", "--entrypoint", "python3", TOOLS_IMAGE,
                        "-B", "/spike/catalog.py", *args], capture_output=True, text=True)
    return p.returncode, pl.redact((p.stdout + p.stderr).strip())


# ---- setup and cleanup -----------------------------------------------------------------------------------

CONTAINERS = [f"{P}-{n}" for n in ("trino", "tools", "lakekeeper", "lakekeeper-migrate", "pg", "minio", "probe", "mc")]


def clear_leftovers() -> tuple[bool, list[str]]:
    """Everything this spike makes, removed; run before (an aborted run's leftovers) and after."""
    wh = bucket = "not checked"
    try:
        wh = rc.wipe(LK_LIVE, WH)
    except Exception as e:  # noqa: BLE001
        wh = f"wipe failed: {e}"
    try:
        bucket = mc_live(f"mc rb --force root/{BUCKET} >/dev/null 2>&1; mc ls root/{BUCKET} >/dev/null 2>&1 "
                         "&& echo present || echo gone").strip()
    except Exception as e:  # noqa: BLE001
        bucket = f"rb failed: {e}"
    pl.remove_containers(*CONTAINERS)
    pl.docker("network", "rm", NET_FENCE, check=False)
    left = pl.docker("ps", "-a", "-q", "--filter", f"label={pl.LABEL}").split()
    nets = pl.docker("network", "ls", "-q", "--filter", f"name={NET_FENCE}").split()
    clean = wh in ("deleted", "absent") and bucket == "gone" and not left and not nets
    return clean, [f"live scratch warehouse {WH}: {wh}", f"live scratch bucket {BUCKET}: {bucket}",
                   f"containers left {len(left)}, network {NET_FENCE} left {len(nets)}"]


def start_live_scratch() -> None:
    out = mc_live(f"mc mb --ignore-existing root/{BUCKET} >/dev/null && echo made")
    st = rc.create_warehouse(LK_LIVE, WH, BUCKET, "http://minio:9000", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD")
    pl.log(f"live scratch: bucket {BUCKET} {out.strip()}, warehouse {WH} HTTP {st}")


def start_fence_lake() -> None:
    """Postgres, MinIO, Lakekeeper (the stack's pins) and a tools container, on NET_FENCE only."""
    user, pw = pl.secret("s01" + secrets.token_hex(6)), pl.secret(secrets.token_hex(20))
    pgpw, key = pl.secret(secrets.token_hex(16)), pl.secret(secrets.token_hex(16))
    db = pl.secret(f"postgres://lk:{pgpw}@{P}-pg:5432/lakekeeper")
    os.environ.update({"S01_FENCE_S3_USER": user, "S01_FENCE_S3_PASSWORD": pw, "POSTGRES_PASSWORD": pgpw,
                       "LAKEKEEPER__PG_DATABASE_URL_READ": db, "LAKEKEEPER__PG_DATABASE_URL_WRITE": db,
                       "LAKEKEEPER__PG_ENCRYPTION_KEY": key,
                       "MC_HOST_fence": pl.secret(f"http://{user}:{pw}@localhost:9000")})
    pl.docker("network", "create", "--label", pl.LABEL, NET_FENCE)
    run = ("run", "-d", "--label", pl.LABEL, "--network", NET_FENCE, "--memory", "256m", "--cpus", "0.5")
    pl.docker(*run, "--name", f"{P}-pg", "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_USER=lk",
              "-e", "POSTGRES_DB=lakekeeper", pl.POSTGRES_IMAGE)
    docker_env({"MINIO_ROOT_USER": user, "MINIO_ROOT_PASSWORD": pw}, *run, "--name", f"{P}-minio",
               "-e", "MINIO_ROOT_USER", "-e", "MINIO_ROOT_PASSWORD", "-e", "MC_HOST_fence", pl.MINIO_IMAGE,
               "server", "/data")
    for _ in range(60):
        if "accepting" in pl.docker("exec", f"{P}-pg", "pg_isready", "-U", "lk", "-d", "lakekeeper", check=False):
            break
        time.sleep(1)
    lk_env = ["-e", "LAKEKEEPER__PG_DATABASE_URL_READ", "-e", "LAKEKEEPER__PG_DATABASE_URL_WRITE",
              "-e", "LAKEKEEPER__PG_ENCRYPTION_KEY"]
    pl.docker("run", "--rm", "--name", f"{P}-lakekeeper-migrate", "--label", pl.LABEL, "--network", NET_FENCE,
              "--memory", "256m", *lk_env, pl.LAKEKEEPER_IMAGE, "migrate")
    pl.docker(*run, "--name", f"{P}-lakekeeper", *lk_env, "-e", f"LAKEKEEPER__BASE_URI={FL}",
              "-e", "LAKEKEEPER__AUDIT__TRACING__ENABLED=false", pl.LAKEKEEPER_IMAGE, "serve")
    pl.docker("run", "-d", "--name", f"{P}-tools", "--label", pl.LABEL, "--network", NET_FENCE, "--memory", "128m",
              "-v", f"{HERE}:/spike:ro", "-v", f"{BASE / 'work'}:/work:ro", "--entrypoint", "sleep", TOOLS_IMAGE,
              "infinity")
    for _ in range(60):
        if subprocess.run(["docker", "exec", f"{P}-lakekeeper", "/home/nonroot/lakekeeper", "healthcheck"],
                          capture_output=True).returncode == 0:
            break
        time.sleep(1)
    for _ in range(60):
        if subprocess.run(["docker", "exec", f"{P}-minio", "mc", "mb", "--ignore-existing", "fence/fence"],
                          capture_output=True).returncode == 0:
            break
        time.sleep(1)
    pl.log("fence lake:", tools("bootstrap", FL)[1], "|",
           tools("create-warehouse", FL, "fence", "fence", f"http://{P}-minio:9000", "S01_FENCE_S3_USER",
                 "S01_FENCE_S3_PASSWORD", env_names=("S01_FENCE_S3_USER", "S01_FENCE_S3_PASSWORD"))[1])


def start_trino() -> None:
    """Trino 484 on k2-net (the live catalog, warehouse spike_s01) and NET_FENCE (the throwaway one)."""
    global PORT
    etc = BASE / "etc"
    live = pl.iceberg_catalog(WH)
    fence = (pl.iceberg_catalog("fence", uri=f"{FL}/catalog").replace("http://minio:9000", f"http://{P}-minio:9000")
             .replace("MINIO_ROOT_USER", "S01_FENCE_S3_USER").replace("MINIO_ROOT_PASSWORD", "S01_FENCE_S3_PASSWORD"))
    reader = "iceberg.security=read_only\niceberg.table-statistics-enabled=false\n"
    pl.write_trino_etc(etc, {"s01": live + reader, "s01_build": live, "fence": fence + reader, "fence_build": fence},
                       extra_files={
                           "access-control.properties": "access-control.name=file\n"
                                                        "security.config-file=/etc/trino/rules.json\n",
                           "group-provider.properties": "group-provider.name=file\nfile.group-file=/etc/trino/groups.txt\n",
                           "rules.json": json.dumps(rules(), indent=1), "groups.txt": GROUPS},
                       config_extra="query.max-history=5000\n", heap="1G")
    name = f"{P}-trino"
    env = [a for n in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "S01_FENCE_S3_USER", "S01_FENCE_S3_PASSWORD")
           for a in ("-e", n)]
    pl.docker("run", "-d", "--name", name, "--label", pl.LABEL, "--memory", "2g", "--cpus", "2",
              "--network", pl.NET, "--network", NET_FENCE, "-p", "127.0.0.1::8080",
              "-v", f"{etc}:/etc/trino:ro", *env, pl.TRINO_IMAGE)
    PORT = int(pl.docker("port", name, "8080/tcp").strip().splitlines()[0].rsplit(":", 1)[1])
    t0 = time.monotonic()
    while time.monotonic() - t0 < 240:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/info", timeout=5) as r:
                if not json.load(r).get("starting", True):
                    pl.log(f"trino up in {time.monotonic() - t0:.0f} s")
                    return
        except OSError:
            pass
        time.sleep(2)
    raise RuntimeError("trino did not start: " + pl.redact(pl.docker("logs", "--tail", "30", name, check=False)))


# ---- h1: the fence matrix --------------------------------------------------------------------------------

META = ["snapshots", "files", "history", "partitions", "refs", "manifests", "all_manifests", "properties",
        "metadata_log_entries", "entries", "all_entries"]


def base_paths(cat: str, ns: str, s_from: int, s_to: int) -> dict[str, str]:
    t = f"{cat}.{ns}.t"
    return {"table": f"SELECT count(*) FROM {t}",
            "version id": f"SELECT count(*) FROM {t} FOR VERSION AS OF {s_to}",
            "version 'audited'": f"SELECT count(*) FROM {t} FOR VERSION AS OF 'audited'",
            "timestamp": f"SELECT count(*) FROM {t} FOR TIMESTAMP AS OF current_timestamp - INTERVAL '1' SECOND",
            **{f"${m}": f'SELECT count(*) FROM {cat}.{ns}."t${m}"' for m in META},
            "table_changes": f"SELECT count(*) FROM TABLE({cat}.system.table_changes('{ns}', 't', {s_from}, {s_to}))",
            "describe": f"DESCRIBE {t}", "show stats": f"SHOW STATS FOR {t}", "show create": f"SHOW CREATE TABLE {t}"}


def h1() -> None:
    for ns in (A, AB, B, BB, X):
        ok(SVC, f"CREATE SCHEMA s01_build.{ns}")
    ok("dbt_a", f"CREATE TABLE s01_build.{AB}.t ({DDL})")
    ok("dbt_a", insert(f"s01_build.{AB}.t", A_TESTED))
    ok("dbt_b", f"CREATE TABLE s01_build.{BB}.t ({DDL})")
    ok("dbt_b", insert(f"s01_build.{BB}.t", B_TESTED))
    s1, b1 = main_id("s01_build", AB, "t", "dbt_a"), main_id("s01_build", BB, "t", "dbt_b")
    for ns, sid in ((AB, s1), (BB, b1)):
        st, doc = LIVE.commit([rc.tag_change(ns, "t", sid, None)])
        assert st in (200, 204), rc.error_text(doc)

    # The design question: the reader catalog is read-only at the connector, so where does a view get made?
    _, err, _ = tq("pub_a", f"CREATE VIEW s01_build.{A}.t SECURITY DEFINER AS {view_sql('s01', A, 't', COLS)}")
    vs = rc.view_state(LIVE.view(A, "t")) if err is None else {}
    rows, rerr, _ = tq(FULL, f"SELECT count(*) FROM s01.{A}.t")
    record("h1.01", "the publisher creates its view through the writer catalog entry, in the reader namespace, its "
           "SQL naming the base through the reader catalog; readers read it through the reader catalog",
           err is None and vs.get("owner") == "pub_a" and rerr is None, f"{short(err)}; reader: {short(rerr)} {rows}; "
           f"stored: {vs}")
    if err:
        raise RuntimeError("no publish route: the rest of h1 depends on it")
    publish_view("s01", B, "t", COLS)
    ok("dbt_a", insert(f"s01_build.{AB}.t", A_UNTESTED))
    s2 = main_id("s01_build", AB, "t", "dbt_a")
    views_before = sorted(LIVE.views(A))
    writes = {"CREATE VIEW": f"CREATE VIEW s01.{A}.y SECURITY DEFINER AS {view_sql('s01', A, 't', COLS)}",
              "CREATE OR REPLACE VIEW t": f"CREATE OR REPLACE VIEW s01.{A}.t AS SELECT 1 x",
              "INSERT": insert(f"s01.{AB}.t", [(9, "open", "zz-sec-9", OLD, 9)]),
              "DELETE": f"DELETE FROM s01.{AB}.t WHERE id = 1",
              "rollback_to_snapshot": f"ALTER TABLE s01.{AB}.t EXECUTE rollback_to_snapshot({s1})",
              "optimize": f"ALTER TABLE s01.{AB}.t EXECUTE optimize",
              "CALL rollback_to_snapshot": f"CALL s01.system.rollback_to_snapshot('{AB}', 't', {s1})",
              "DROP TABLE": f"DROP TABLE s01.{AB}.t", "DROP VIEW": f"DROP VIEW s01.{A}.t"}
    res = {k: tq("pub_a", sql)[1] for k, sql in writes.items()}
    unchanged = main_id("s01_build", AB, "t", "dbt_a") == s2 and sorted(LIVE.views(A)) == views_before
    record("h1.02", "so the publisher holds catalog access ALL on the reader catalog (the definer-view owner check "
           "requires it); the read-only connector still refuses it every write there, and nothing changed",
           all(res.values()) and unchanged, {k: short(e) for k, e in res.items()})

    # Query history right after the builder's INSERT, whose text carries the untested values.
    rows, err, _ = tq(FULL, 'SELECT count(*) FROM system.runtime.queries WHERE "user" <> current_user')
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/query", headers={"X-Trino-User": FULL})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            others: list | str = [q for q in json.load(r) if q["session"]["user"] != FULL]
    except (OSError, KeyError, ValueError) as e:
        others = f"error {e}"
    ctl = ok(SVC, "SELECT count(*) FROM system.runtime.queries WHERE \"user\" = 'dbt_a' AND query LIKE '%444444%'")
    record("h1.03", "a reader sees no other principal's query text (system.runtime.queries, /v1/query); "
           "svc.platform does, so the history held the builder's INSERT",
           (rows == [[0]] or denied(err)) and others == [] and ctl[0][0] > 0,
           f"reader: table {rows or short(err)}, REST {others if isinstance(others, str) else len(others)} foreign "
           f"queries; svc.platform sees {ctl[0][0]} of dbt_a's")

    want_full = [[1, "open", "zz-sec-1", 10], [2, "open", "zz-sec-2", 20], [5, "open", "zz-sec-5", 555555]]
    got = ok(FULL, f"SELECT id, region, secret, amount FROM s01.{A}.t ORDER BY id")
    record("h1.04", "full reader: exactly the tagged rows, row scope applied", got == want_full, got)
    want_delayed = [[1, "open", None, 10], [2, "open", None, 20]]
    got = ok(DELAYED, f"SELECT id, region, secret, amount FROM s01.{A}.t ORDER BY id")
    got_pred = ok(DELAYED, f"SELECT count(*) FROM s01.{A}.t WHERE secret = 'zz-sec-1'")
    record("h1.05", "delayed reader: the tagged rows with scope, the 20-minute delay and the mask (a predicate on "
           "the masked column matches nothing)", got == want_delayed and got_pred == [[0]], f"{got}; {got_pred}")
    u4 = {u: ok(u, f"SELECT count(*) FROM s01.{A}.t WHERE id = 4") for u in (FULL, DELAYED)}
    main4 = ok("dbt_a", f"SELECT count(*) FROM s01_build.{AB}.t WHERE id = 4")
    record("h1.06", "the untested row is never returned, though main holds it", u4 == {FULL: [[0]], DELAYED: [[0]]}
           and main4 == [[1]], f"readers {u4}; builder on main {main4}")
    _, e1, _ = tq(OTHER, f"SELECT count(*) FROM s01.{A}.t")
    rows, e2, _ = tq(OTHER, "SELECT table_schema, table_name FROM s01.information_schema.tables "
                            "WHERE table_schema LIKE 'spike_s01%'")
    record("h1.07", "an unrelated user: the view denied, nothing listed", denied(e1) and (bool(e2) or rows == []),
           f"{short(e1)}; listing {rows or short(e2)}")

    # Every base path, every reader, both catalogs, both products; each statement also run by the
    # builder through the writer catalog, so a refusal is the fence and not a typo.
    total, refused, bad, invalid, kinds = 0, 0, [], [], {}
    for ns, builder, (sa, sb) in ((AB, "dbt_a", (s1, s2)), (BB, "dbt_b", (b1, b1))):
        controls = base_paths("s01_build", ns, sa, sb)
        for cat in ("s01", "s01_build"):
            for key, sql in base_paths(cat, ns, sa, sb).items():
                if cat == "s01_build" and key != "table_changes":
                    _, cerr, _ = tq(builder, controls[key])
                    if cerr:
                        invalid.append(f"{key}: {cerr[:80]}")
                for u in (FULL, DELAYED, OTHER):
                    total += 1
                    rows, err, _ = tq(u, sql)
                    if denied(err):
                        refused += 1
                        kind = re.sub(r" [a-z0-9_.$\"]+$", "", err.split("Access Denied: ", 1)[1])[:40]
                        kinds[kind] = kinds.get(kind, 0) + 1
                    else:
                        bad.append(f"{u} {cat}.{ns} {key}: {err or rows}")
    record("h1.08", f"every base path refused to every reader: table, time travel by id, 'audited' and timestamp, "
           f"{len(META)} metadata tables, table_changes, DESCRIBE, SHOW STATS, SHOW CREATE; "
           f"both catalogs, both products", not bad and not invalid,
           f"{refused}/{total} refused {kinds}; not refused {bad[:3]}; invalid controls {invalid}")

    errs = {k: tq(FULL, f"SELECT count(*) FROM s01.{A}.t {v}")[1]
            for k, v in (("id", f"FOR VERSION AS OF {s2}"), ("timestamp", "FOR TIMESTAMP AS OF current_timestamp"))}
    record("h1.09", "time travel on the view, by snapshot id and by timestamp, is refused",
           all(errs.values()), {k: short(e) for k, e in errs.items()})
    strs = {v: ok(FULL, f"SELECT id FROM s01.{A}.t FOR VERSION AS OF '{v}' ORDER BY id") for v in ("main", "zzz")}
    record("h1.10", "a string version on the view is ignored, never honoured: 'main' and 'zzz' both return the "
           "tag's rows", all(r == [[1], [2], [5]] for r in strs.values()), strs)
    errs = {m: tq(FULL, f'SELECT count(*) FROM s01.{A}."t${m}"')[1] for m in ("snapshots", "files", "refs")}
    _, ebld, _ = tq(FULL, f"SELECT count(*) FROM s01_build.{A}.t")
    record("h1.11", "the view has no metadata tables, and readers cannot reach it through the writer catalog",
           all(errs.values()) and denied(ebld), f"{ {m: short(e) for m, e in errs.items()} }; writer catalog: "
           f"{short(ebld)}")

    before = rc.view_state(LIVE.view(A, "t"))
    no_tag = f"SELECT {', '.join(COLS)} FROM s01_build.{AB}.t"
    attempts = {
        "dbt_a CREATE VIEW A.x": ("dbt_a", f"CREATE VIEW s01_build.{A}.x SECURITY DEFINER AS {no_tag}"),
        "dbt_a CREATE OR REPLACE VIEW A.t": ("dbt_a", f"CREATE OR REPLACE VIEW s01_build.{A}.t SECURITY DEFINER AS {no_tag}"),
        "dbt_a DROP VIEW A.t": ("dbt_a", f"DROP VIEW s01_build.{A}.t"),
        "dbt_a RENAME A.t": ("dbt_a", f"ALTER VIEW s01_build.{A}.t RENAME TO s01_build.{A}.t_old"),
        "dbt_a COMMENT ON VIEW A.t": ("dbt_a", f"COMMENT ON VIEW s01_build.{A}.t IS 'x'"),
        "dbt_a CREATE TABLE A.t2": ("dbt_a", f"CREATE TABLE s01_build.{A}.t2 AS {no_tag}"),
        "dbt_a CREATE OR REPLACE VIEW s01.A.t": ("dbt_a", f"CREATE OR REPLACE VIEW s01.{A}.t AS SELECT 1 x"),
        "dbt_b CREATE OR REPLACE VIEW A.t": ("dbt_b", f"CREATE OR REPLACE VIEW s01_build.{A}.t AS SELECT 1 x"),
        "pub_b CREATE OR REPLACE VIEW A.t": ("pub_b", f"CREATE OR REPLACE VIEW s01_build.{A}.t AS SELECT 1 x"),
    }
    res = {k: tq(u, sql)[1] for k, (u, sql) in attempts.items()}
    after = rc.view_state(LIVE.view(A, "t"))
    record("h1.12", "builders, another product's builder and another publisher cannot create, replace, drop, rename "
           "or comment a reader view, or put a table in the reader namespace; the view is unchanged",
           all(res.values()) and before == after, {k: short(e) for k, e in res.items()})

    _, e_make, _ = tq("pub_b", f"CREATE VIEW s01_build.{B}.leak SECURITY DEFINER AS {view_sql('s01', A, 't', COLS)}")
    e_read = tq(FULL, f"SELECT count(*) FROM s01.{B}.leak")[1] if e_make is None else "not reached"
    record("h1.13", "publisher B (no privilege on A's base) cannot serve A's rows through a view of its own",
           e_make is not None or denied(e_read), f"create: {short(e_make)}; read: {short(e_read)}")
    _, e_make, _ = tq("pub_x", f"CREATE VIEW s01_build.{X}.v SECURITY DEFINER AS {view_sql('s01', A, 't', COLS)}")
    rows, e_read, _ = tq(FULL, f"SELECT count(*) FROM s01.{X}.v")
    record("h1.14", "a publisher with SELECT but no GRANT_SELECT on A's base: its definer view is created, reading "
           "it is refused (GRANT_SELECT is the boundary)",
           e_make is None and denied(e_read) and "cannot create view that selects from" in (e_read or ""),
           f"create: {short(e_make)}; read: {short(e_read)}")
    _, e_make, _ = tq("pub_a", f"CREATE VIEW s01_build.{A}.inv SECURITY INVOKER AS {view_sql('s01', A, 't', COLS)}")
    _, e_read, _ = tq(FULL, f"SELECT count(*) FROM s01.{A}.inv")
    record("h1.15", "an invoker view over the base gives a reader nothing (no reader holds a base privilege)",
           e_make is None and denied(e_read), f"create: {short(e_make)}; read: {short(e_read)}")
    mv = {u: tq(u, f"CREATE MATERIALIZED VIEW {cat}.mv AS SELECT 1 x")[1]
          for u, cat in (("dbt_a", f"s01_build.{AB}"), ("pub_a", f"s01_build.{A}"))}
    record("h1.16", "materialized views cannot be created on the REST catalog", all(mv.values()),
           {u: short(e) for u, e in mv.items()})
    _, e_cp, _ = tq("dbt_b", f"CREATE TABLE s01_build.{BB}.copy AS SELECT id, secret FROM s01.{AB}.t")
    copied = ok("dbt_b", f"SELECT id FROM s01_build.{BB}.copy ORDER BY id") if e_cp is None else e_cp
    record("h1.17", "B4 (known, held by k2_input and the lint): a builder with SELECT on an input's base copies its "
           "untested main into its own base", None, f"create: {short(e_cp)}; copied ids {copied}")
    errs = {u: tq(u, f"SELECT count(*) FROM s01.{BB}.copy")[1] for u in (FULL, DELAYED, OTHER)}
    record("h1.18", "no reader can read that copy: it sits in a base namespace", all(denied(e) for e in errs.values()),
           {u: short(e) for u, e in errs.items()})

    auth = {"pub_a: view A.t to pub_b": ("pub_a", f"ALTER VIEW s01_build.{A}.t SET AUTHORIZATION USER pub_b"),
            "pub_b: view B.t to pub_a": ("pub_b", f"ALTER VIEW s01_build.{B}.t SET AUTHORIZATION USER pub_a"),
            "pub_a: schema A to dbt_a": ("pub_a", f"ALTER SCHEMA s01_build.{A} SET AUTHORIZATION USER dbt_a"),
            "dbt_a: schema AB to pub_a": ("dbt_a", f"ALTER SCHEMA s01_build.{AB} SET AUTHORIZATION USER pub_a"),
            "dbt_a: table AB.t to pub_a": ("dbt_a", f"ALTER TABLE s01_build.{AB}.t SET AUTHORIZATION USER pub_a"),
            "svc.platform: view A.t to itself": (SVC, f'ALTER VIEW s01_build.{A}.t SET AUTHORIZATION USER "svc.platform"')}
    res = {k: tq(u, sql)[1] for k, (u, sql) in auth.items()}
    owners = (rc.view_state(LIVE.view(A, "t"))["owner"], rc.view_state(LIVE.view(B, "t"))["owner"])
    record("h1.19", "SET AUTHORIZATION on a view, schema or table is refused to every principal (no authorization "
           "rules), and the owners are unchanged", all(res.values()) and owners == ("pub_a", "pub_b"),
           {k: short(e) for k, e in res.items()})

    # Statistics: the reader catalogs run with them off; what is left, and can a reader turn them on?
    leaks, left = [], {}
    for u in (FULL, DELAYED):
        for sql in (f"SHOW STATS FOR s01.{A}.t", f"SHOW STATS FOR (SELECT * FROM s01.{A}.t)"):
            rows = ok(u, sql)
            leaks += [f"{u}: {r}" for r in stats_bounds(rows)] + [f"{u}: {s}" for s in HIDDEN[u] if s in json.dumps(rows)]
            left[f"{u} {sql.split(' FOR ')[1][:12]}"] = stats_left(rows)
    record("h1.20", "SHOW STATS on the view (and on a query over it): no low or high bound, no row count, no hidden "
           "value", not leaks, f"left visible: {left}; leaks {leaks}")
    for u in (FULL, DELAYED):
        rows, err, _ = tq(u, f"EXPLAIN ANALYZE VERBOSE SELECT * FROM s01.{A}.t")
        text = "\n".join(r[0] for r in rows)
        found = [s for s in HIDDEN[u] if s in text]
        inputs = re.findall(r"Input: (\d+) rows", text)
        est = sorted(set(re.findall(r"Estimates: \{rows: ([^ ,]+)", text)))
        scan = re.search(r"table = (\S+)", text)
        record(f"h1.21{u[9]}", f"EXPLAIN ANALYZE VERBOSE as {u}: no hidden value, no estimate from statistics",
               err is None and not found and set(est) <= {"?"},
               f"hidden values found {found}; estimates {est}; Input rows {inputs}; "
               f"scan {scan.group(1) if scan else None}")
    countable = {}
    for u, visible in ((FULL, 3), (DELAYED, 2)):
        rows, err, st = tq(u, f"SELECT count(*) FROM s01.{A}.t")
        countable[u] = f"{st.get('processedRows', 0) - visible} of {st.get('processedRows')}"
        record(f"h1.22{u[9]}", f"{u}: the processed-rows counter counts the tagged snapshot only, never the untested row",
               rows == [[visible]] and st.get("processedRows") == len(A_TESTED),
               f"returned {rows}; client protocol stats processedRows {st.get('processedRows')}, processedBytes "
               f"{st.get('processedBytes')}, physicalInputBytes {st.get('physicalInputBytes')}; so "
               f"{st.get('processedRows', 0) - visible} rows hidden by policy are countable (Q2)")
    record("h1.22q", "Q2, not a criterion: a reader can count the tagged rows its policy hides (processedRows in the "
           "client protocol, Input rows in EXPLAIN ANALYZE), not read them", None, countable)
    res, hdr = {}, {}
    for u in (FULL, DELAYED, OTHER):
        res[u] = tq(u, "SET SESSION s01.statistics_enabled = true")[1]
        rows, err, _ = tq(u, f"SHOW STATS FOR s01.{A}.t", session="s01.statistics_enabled=true")
        hdr[u] = err if err else ("BOUNDS " + str(stats_bounds(rows)) if stats_bounds(rows) else "accepted, no bounds")
    record("h1.23", "a reader's SET SESSION s01.statistics_enabled = true is refused by the rule",
           all(denied(e) for e in res.values()), {u: short(e) for u, e in res.items()})
    record("h1.24", "the same property in the session header (the CLI's --session) is refused too",
           all(denied(h) for h in hdr.values()), {u: short(h) for u, h in hdr.items()})
    rows, err, _ = tq("pub_a", f"SHOW STATS FOR s01.{A}.t", session="s01.statistics_enabled=true")
    record("h1.25", "control: a pipeline principal may set it, and the bounds come back (so the rule is what holds)",
           err is None and bool(stats_bounds(rows)), short(err) if err else stats_left(rows))
    props = ok(FULL, "SHOW SESSION LIKE 's01.%'")
    booleans = [(r[0], r[2]) for r in props if r[3] == "boolean" and r[0] != "s01.statistics_enabled"]
    flips: dict[str, list] = {"bounds": [], "refused": [], "other error": [], "accepted, no bounds": []}
    for name, default in booleans:
        rows, err, _ = tq(FULL, f"SHOW STATS FOR s01.{A}.t", session=f"{name}={'false' if default == 'true' else 'true'}")
        key = "refused" if denied(err) else "other error" if err else "bounds" if stats_bounds(rows) else "accepted, no bounds"
        flips[key].append(name.removeprefix("s01."))
    record("h1.26", f"no other boolean catalog session property a reader may set brings a bound back "
           f"({len(booleans)} flipped one at a time)", not flips["bounds"], flips)
    _, err, _ = tq(FULL, "SET SESSION s01.expire_snapshots_min_retention = '0s'")
    record("h1.27", "a reader's retention session property is refused", denied(err), short(err))
    _, err, _ = tq(FULL, f"SHOW CREATE VIEW s01.{A}.t")
    record("h1.28", "SHOW CREATE VIEW is refused to a reader", denied(err), short(err))
    vrows = {u: tq(u, "SELECT table_schema, table_name, view_definition FROM s01.information_schema.views "
                      "WHERE table_schema LIKE 'spike_s01%' ORDER BY 1, 2")[0:2] for u in (FULL, DELAYED, OTHER)}
    record("h1.29", "B5: what information_schema.views shows each reader", None,
           {u: (r[1] or [f"{x[0]}.{x[1]}: {' '.join(x[2].split())}" for x in r[0]]) for u, r in vrows.items()})
    listed = {u: tq(u, "SELECT table_schema, table_name FROM s01.information_schema.tables "
                       "WHERE table_schema LIKE 'spike_s01%' ORDER BY 1, 2")[0] for u in (FULL, DELAYED)}
    shown = {u: tq(u, f"SHOW TABLES FROM s01.{AB}") for u in (FULL, DELAYED)}
    record("h1.30", "no base table is listed to a reader (information_schema.tables, SHOW TABLES)",
           not any(r[0].endswith("__base") for v in listed.values() for r in v)
           and all(e or r == [] for r, e, _ in shown.values()),
           f"listed {listed}; SHOW TABLES on a base {[short(e) if e else r for r, e, _ in shown.values()]}")

    _, e_make, _ = tq(SVC, f"CREATE VIEW s01_build.{A}.foreign_view SECURITY DEFINER AS {no_tag}")
    _, e_read, _ = tq(FULL, f"SELECT count(*) FROM s01.{A}.foreign_view")
    record("h1.31", "a view planted in A's reader namespace under a name no rule grants is denied to readers",
           denied(e_read), f"planted by svc.platform: {short(e_make)}; read: {short(e_read)}")
    record("h1.32", "the view as the catalog stores it (the drift check's input)", None, rc.view_state(LIVE.view(A, "t")))

    # Last, as both change A's view or table: two ways a pipeline principal can break the view; both fail closed.
    _, e_make, _ = tq(SVC, f"CREATE OR REPLACE VIEW s01_build.{A}.t SECURITY DEFINER AS {no_tag}")
    rows, e_read, _ = tq(FULL, f"SELECT id FROM s01.{A}.t ORDER BY id")
    owner = rc.view_state(LIVE.view(A, "t"))["owner"]
    record("h1.33", "svc.platform (OWNERSHIP, no GRANT_SELECT) replacing a reader view without the tag: readers get "
           "an error, not rows", e_make is not None or (e_read is not None and not rows),
           f"replace: {short(e_make)}; owner now {owner}; reader: {short(e_read) if e_read else rows}")
    publish_view("s01", A, "t", COLS)
    ok("dbt_a", f"DROP TABLE s01_build.{AB}.t")
    ok("dbt_a", f"CREATE TABLE s01_build.{AB}.t ({DDL})")
    ok("dbt_a", insert(f"s01_build.{AB}.t", A_TESTED + A_UNTESTED))
    rows, e_read, _ = tq(FULL, f"SELECT id FROM s01.{A}.t ORDER BY id")
    record("h1.34", "a builder that drops and recreates its base (the tag goes with the old table): readers get an "
           "error, never the new rows", e_read is not None and not rows, short(e_read) if e_read else rows)


# ---- h2: the polled cycle ----------------------------------------------------------------------------------

OUT = ("t1", "t2")
TYPES = {"id": "bigint", "batch": "bigint", "flag": "varchar", "amount": "bigint", "total": "bigint",
         "note": "varchar", "note2": "varchar"}
CONTRACT = {"t1": ["id", "batch", "flag", "amount"], "t2": ["id", "batch", "flag", "total"]}
AUDITED: dict[str, int | None] = {"t1": None, "t2": None}
COMMITS: list[tuple[float, float, int]] = []    # (sent, returned, batch), time.monotonic()
PUBLISHED: dict[int, tuple[int, int]] = {}      # batch -> (rows in t1, rows in t2) when tested
VIEW_MS: list[float] = []
POLL_SQL = (f"SELECT a.n, a.b, a.bad, b.n, b.b, b.bad FROM "
            f"(SELECT count(*) n, max(batch) b, count_if(flag <> 'ok') bad FROM s01.{C}.t1) a CROSS JOIN "
            f"(SELECT count(*) n, max(batch) b, count_if(flag <> 'ok') bad FROM s01.{C}.t2) b")


class Poller(threading.Thread):
    """The reader: both views in one query about every 50 ms, on connections of its own."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.polls: list[tuple[float, float, list | None, str | None]] = []

    def run(self) -> None:
        while not self.stop.is_set():
            ps = time.monotonic()
            try:
                rows, err, _ = tq(FULL, POLL_SQL)
            except Exception as e:  # noqa: BLE001
                rows, err = [], f"client: {e}"
            self.polls.append((ps, time.monotonic(), rows[0] if rows else None, err))
            time.sleep(0.05)


def check_polls(polls: list, commits: list, published: dict) -> dict[str, list]:
    """Each poll saw, per table, the batch of some publish whose commit was sent before the poll
    ended; never a row of a failed or unpublished build, never an empty or partly loaded table.
    The two tables may differ only while a commit was in flight during that poll."""
    out: dict[str, list] = {k: [] for k in ("good", "errors", "empty", "untested", "torn", "mixed_in_flight",
                                            "mixed_outside", "stale")}
    for i, (ps, pe, row, err) in enumerate(polls):
        if err:
            out["errors"].append((i, err))
            continue
        out["good"].append(i)
        n1, b1, bad1, n2, b2, bad2 = row
        if not n1 or not n2:
            out["empty"].append(i)
            continue
        lo = max((b for s, r, b in commits if r < ps), default=-1)   # newest publish complete before the poll
        hi = max((b for s, r, b in commits if s < pe), default=-1)   # newest publish begun before it ended
        if bad1 or bad2 or b1 > hi or b2 > hi or b1 not in published or b2 not in published:
            out["untested"].append(i)
            continue
        if n1 != published[b1][0] or n2 != published[b2][1]:
            out["torn"].append(i)
        if b1 != b2:
            out["mixed_in_flight" if lo < hi else "mixed_outside"].append(i)
        if min(b1, b2) < lo:
            out["stale"].append(i)
    return out


def selfcheck() -> bool:
    commits = [(1.0, 1.02, 0), (10.0, 10.02, 1), (20.0, 20.02, 2)]
    published = {0: (2, 1), 1: (4, 2), 2: (6, 3)}
    polls = [(5.0, 5.1, [2, 0, 0, 1, 0, 0], None),          # 0 clean
             (15.0, 15.1, [6, 2, 0, 3, 2, 0], None),        # 1 batch 2 before its commit
             (15.0, 15.1, [4, 1, 1, 2, 1, 0], None),        # 2 a failed build's row
             (15.0, 15.1, [0, None, 0, 2, 1, 0], None),     # 3 empty
             (15.0, 15.1, [3, 1, 0, 2, 1, 0], None),        # 4 torn
             (15.0, 15.1, [4, 1, 0, 1, 0, 0], None),        # 5 mixed with no commit in flight, and stale
             (19.99, 20.01, [6, 2, 0, 2, 1, 0], None),      # 6 mixed while commit 2 was in flight
             (25.0, 25.1, [4, 1, 0, 2, 1, 0], None),        # 7 stale
             (5.0, 5.1, None, "boom")]                       # 8 error
    return check_polls(polls, commits, published) == {
        "good": [0, 1, 2, 3, 4, 5, 6, 7], "errors": [(8, "boom")], "empty": [3], "untested": [1, 2], "torn": [4], "mixed_in_flight": [6],
        "mixed_outside": [5], "stale": [5, 7]}


def columns(t: str) -> list[str]:
    return rc.schema_columns(LIVE.table(CB, t))


def h2_insert(t: str, batch: int, flag: str = "ok") -> None:
    cols = columns(t)
    val = {"batch": str(batch), "flag": f"'{flag}'", "amount": str(batch), "total": str(batch),
           "note": f"'n{batch}'", "note2": f"'m{batch}'"}
    ids = (batch * 10 + 1, batch * 10 + 2) if t == "t1" else (batch * 10 + 1,)
    values = ", ".join("(" + ", ".join(str(i) if c == "id" else val[c] for c in cols) + ")" for i in ids)
    ok("dbt_c", f"INSERT INTO s01_build.{CB}.{t} ({', '.join(cols)}) VALUES {values}")


def h2_rebuild(t: str) -> None:
    """dbt's contracted path on a column change (spike f): an empty create-or-replace, then the insert."""
    meta = LIVE.table(CB, t)
    prev, old, new = rc.ref_id(meta, "main"), rc.schema_columns(meta), CONTRACT[t]
    ok("dbt_c", f"CREATE OR REPLACE TABLE s01_build.{CB}.{t} ({', '.join(f'{c} {TYPES[c]}' for c in new)})")
    sel = ", ".join(c if c in old else f"CAST(NULL AS {TYPES[c]})" for c in new)
    ok("dbt_c", f"INSERT INTO s01_build.{CB}.{t} ({', '.join(new)}) SELECT {sel} "
                f"FROM s01_build.{CB}.{t} FOR VERSION AS OF {prev}")


def h2_test() -> tuple[bool, dict]:
    counts, passed = {}, True
    for t in OUT:
        n, bad = ok("dbt_c", f"SELECT count(*), count_if(flag <> 'ok') FROM s01_build.{CB}.{t}")[0]
        counts[t], passed = n, passed and bad == 0
    return passed, counts


def h2_view(t: str, cols: list[str]) -> bool:
    """Replace the view only when its columns differ; True when it did."""
    meta = LIVE.view(C, t)
    if meta is not None and rc.view_state(meta)["columns"] == cols:
        return False
    t0 = time.monotonic()
    publish_view("s01", C, t, cols)
    VIEW_MS.append((time.monotonic() - t0) * 1000)
    return True


def h2_publish(batch: int, counts: dict, kill_hook=None) -> None:
    """A7 step 6 with condition 4: views to C ∩ old ∩ new, one multi-table tag commit, views to C ∩ new."""
    tested = {t: rc.ref_id(LIVE.table(CB, t), "main") for t in OUT}
    for t in OUT:
        if AUDITED[t] is not None:
            meta = LIVE.table(CB, t)
            old, new = rc.schema_columns(meta, AUDITED[t]), rc.schema_columns(meta, tested[t])
            h2_view(t, [c for c in CONTRACT[t] if c in old and c in new])
    t0 = time.monotonic()
    st, doc = LIVE.commit([rc.tag_change(CB, t, tested[t], AUDITED[t]) for t in OUT])
    t1 = time.monotonic()
    if st not in (200, 204):
        raise RuntimeError(f"tag commit for batch {batch}: HTTP {st} {rc.error_text(doc)}")
    COMMITS.append((t0, t1, batch))
    PUBLISHED[batch] = (counts["t1"], counts["t2"])
    AUDITED.update(tested)
    if kill_hook:
        kill_hook()
    for t in OUT:
        h2_view(t, [c for c in CONTRACT[t] if c in rc.schema_columns(LIVE.table(CB, t), tested[t])])


def h2_reconcile() -> list[str]:
    """A7 step 1's view rule: each view equals C ∩ the tagged snapshot's columns. Idempotent."""
    changed = []
    for t in OUT:
        meta = LIVE.table(CB, t)
        want = [c for c in CONTRACT[t] if c in rc.schema_columns(meta, rc.ref_id(meta, "audited"))]
        if h2_view(t, want):
            changed.append(f"{t}: {want}")
    return changed


def publish_child(state: str) -> None:
    """The publisher as its own process, for (v): it stops after the tag commit and is SIGKILLed there."""
    global PORT, LIVE
    s = json.loads(state)
    PORT, LIVE = s["port"], rc.Catalog(f"{LK_LIVE}/catalog", WH)
    CONTRACT.update(s["contract"])
    AUDITED.update(s["audited"])

    def hook() -> None:
        print(json.dumps({"commit": COMMITS[-1], "audited": AUDITED}), flush=True)
        print("TAGGED", flush=True)
        sys.stdin.readline()  # the parent kills this process here
    h2_publish(s["batch"], {"t1": 0, "t2": 0}, kill_hook=hook)


def described(t: str) -> list[str]:
    return [r[0] for r in ok(FULL, f"DESCRIBE s01.{C}.{t}")]


def h2() -> None:
    global POLLER
    record("h2.00", "the poll checker flags a planted untested row, failed-build row, empty, torn, unstraddled "
           "mixed, stale and erroring poll (self-check on synthetic polls)", selfcheck(), "check_polls()")
    for ns in (C, CB):
        ok(SVC, f"CREATE SCHEMA s01_build.{ns}")
    for t in OUT:
        ok("dbt_c", f"CREATE TABLE s01_build.{CB}.{t} ({', '.join(f'{c} {TYPES[c]}' for c in CONTRACT[t])})")
    batch, spans = 0, {}

    def build() -> None:
        nonlocal batch
        batch += 1
        for t in OUT:
            h2_insert(t, batch)
        passed, counts = h2_test()
        assert passed, f"batch {batch} failed its test"
        h2_publish(batch, counts)

    for t in OUT:
        h2_insert(t, 0)
    h2_publish(0, h2_test()[1])  # the first publish: tags first, then the views
    POLLER = Poller()
    POLLER.start()
    time.sleep(1)
    t_start = time.monotonic()
    for _ in range(8):
        build()

    # (ii) a failing build: one table's test fails, so neither tag moves; both tables are restored.
    spans["ii"] = [time.monotonic()]
    batch += 1
    before = {t: rc.ref_id(LIVE.table(CB, t), "main") for t in OUT}
    h2_insert("t1", batch, flag="bad")
    h2_insert("t2", batch)
    passed, _ = h2_test()
    tags = dict(AUDITED)
    for t in OUT:
        ok("dbt_c", f"CREATE OR REPLACE TABLE s01_build.{CB}.{t} AS SELECT * FROM s01_build.{CB}.{t} "
                    f"FOR VERSION AS OF {before[t]}")
    moved = {t: rc.ref_id(LIVE.table(CB, t), "audited") for t in OUT}
    same = {t: ok("dbt_c", f"SELECT count(*), sum(id) FROM s01_build.{CB}.{t}")
            == ok("dbt_c", f"SELECT count(*), sum(id) FROM s01_build.{CB}.{t} FOR VERSION AS OF {AUDITED[t]}")
            for t in OUT}
    spans["ii"].append(time.monotonic())
    record("h2.01", "(ii) a failing build: its test fails, neither tag moves, and CREATE OR REPLACE ... AS SELECT * "
           "FROM t FOR VERSION AS OF <id> restores main to the tagged rows", not passed and moved == tags
           and all(same.values()), f"test passed {passed}; tags unchanged {moved == tags}; main = tag {same}")
    for _ in range(3):
        build()

    # (iii) an added column: views unchanged until the commit, then gain it.
    spans["iii"] = [time.monotonic()]
    batch += 1
    CONTRACT["t1"].append("note")
    h2_rebuild("t1")
    h2_insert("t1", batch)
    h2_insert("t2", batch)
    passed, counts = h2_test()
    h2_publish(batch, counts)
    spans["iii"].append(time.monotonic())
    d = described("t1")
    record("h2.02", "(iii) an added column: the view gains it after the tag moves", "note" in d, d)
    for _ in range(3):
        build()

    # (iv) a dropped column with the rule: the view loses it before the tag moves.
    spans["iv"] = [time.monotonic()]
    batch += 1
    CONTRACT["t1"].remove("amount")
    h2_rebuild("t1")
    h2_insert("t1", batch)
    h2_insert("t2", batch)
    passed, counts = h2_test()
    h2_publish(batch, counts)
    spans["iv"].append(time.monotonic())
    d = described("t1")
    record("h2.03", "(iv) a dropped column under the rule (C ∩ old ∩ new, tag, C ∩ new): the view drops it first",
           "amount" not in d, d)
    for _ in range(2):
        build()

    # (v) an added column, and the publisher killed between the tag commit and the view update.
    spans["v"] = [time.monotonic()]
    batch += 1
    CONTRACT["t2"].append("note2")
    h2_rebuild("t2")
    h2_insert("t1", batch)
    h2_insert("t2", batch)
    passed, counts = h2_test()
    state = json.dumps({"port": PORT, "contract": CONTRACT, "audited": AUDITED, "batch": batch})
    child = subprocess.Popen([sys.executable, "-B", str(HERE / "spike.py"), "publish-child", state],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    info = None
    for line in child.stdout:
        if line.startswith("{"):
            info = json.loads(line)
        if line.strip() == "TAGGED":
            break
    child.kill()
    child.wait()
    if info is None:
        raise RuntimeError("publish child died before the tag commit: " + pl.redact(child.stderr.read()[-600:]))
    COMMITS.append(tuple(info["commit"]))
    PUBLISHED[batch] = (counts["t1"], counts["t2"])
    AUDITED.update(info["audited"])
    time.sleep(1)  # polls run against the half-published state
    stale_view = described("t2")
    changed = h2_reconcile()
    again = h2_reconcile()
    spans["v"].append(time.monotonic())
    d = described("t2")
    record("h2.04", "(v) a kill between the tag commit and the view update: reads go on with the old columns; the "
           "reconcile completes it, and a second reconcile changes nothing", child.returncode == -9
           and "note2" not in stale_view and "note2" in d and bool(changed) and not again,
           f"child exit {child.returncode}; t2 after the kill {stale_view}; reconcile {changed}; again {again}; "
           f"now {d}")
    for _ in range(3):
        build()

    # The commit is atomic: one stale requirement and neither tag moves (a build left untested in main).
    for t in OUT:
        h2_insert(t, batch + 1)
    tested = {t: rc.ref_id(LIVE.table(CB, t), "main") for t in OUT}
    st, doc = LIVE.commit([rc.tag_change(CB, "t1", tested["t1"], AUDITED["t1"]),
                           rc.tag_change(CB, "t2", tested["t2"], (AUDITED["t2"] or 0) + 1)])
    moved = {t: rc.ref_id(LIVE.table(CB, t), "audited") for t in OUT}
    record("h2.05", "one stale requirement in the multi-table commit: 409, and neither tag moves",
           st == 409 and moved == AUDITED, f"HTTP {st} {rc.error_text(doc)}; tags unchanged {moved == AUDITED}")
    time.sleep(1)
    POLLER.stop.set()
    POLLER.join(timeout=30)
    t_end = time.monotonic()

    polls = POLLER.polls
    r = check_polls(polls, COMMITS, PUBLISHED)
    ms = sorted((s2 - s1) * 1000 for s1, s2, _ in COMMITS)
    lat = sorted((pe - ps) * 1000 for ps, pe, _, _ in polls)
    in_span = {k: sum(1 for ps, *_ in polls if v[0] <= ps <= v[1]) for k, v in spans.items()}
    record("h2.06", f"multi-table tag commit (2 tables), from the host: n={len(ms)}", len(ms) >= 20,
           f"median {statistics.median(ms):.1f} ms, p95 {ms[int(0.95 * (len(ms) - 1))]:.1f} ms, max {ms[-1]:.1f} ms")
    record("h2.07", f"reader polls: {len(polls)} over {t_end - t_start:.0f} s; none errored, under the view rule",
           len(r["good"]) > 0 and not r["errors"],
           f"{len(r['good'])} answered; poll latency median {statistics.median(lat):.0f} ms, max {lat[-1]:.0f} ms; polls during (ii) "
           f"{in_span.get('ii')}, (iii) {in_span.get('iii')}, (iv) {in_span.get('iv')}, (v) {in_span.get('v')}; "
           f"errors {r['errors'][:3]}")
    record("h2.08", "no poll returned an untested row (a failed build's, or a batch before its commit was sent)",
           len(r["good"]) > 0 and not r["untested"], f"{len(r['untested'])} of {len(r['good'])} answered polls {[polls[i] for i in r['untested'][:3]]}")
    record("h2.09", "no poll returned an empty table", len(r["good"]) > 0 and not r["empty"],
           f"{len(r['empty'])} of {len(r['good'])} answered polls")
    record("h2.10", "no poll returned a partly loaded table (rows = the tested count of the batch seen)",
           len(r["good"]) > 0 and not r["torn"], f"{len(r['torn'])} of {len(r['good'])} answered polls {[polls[i] for i in r['torn'][:3]]}")
    record("h2.11", "the two tables disagree only while a commit is in flight during that very poll",
           len(r["good"]) > 0 and not r["mixed_outside"],
           f"mixed polls: {len(r['mixed_in_flight'])} during a commit, {len(r['mixed_outside'])} outside one, of "
           f"{len(r['good'])} answered; {[polls[i] for i in (r['mixed_in_flight'] + r['mixed_outside'])[:3]]}")
    record("h2.12", "polls that started after a commit returned yet showed an older batch", None,
           f"{len(r['stale'])} of {len(r['good'])} answered")
    if VIEW_MS:
        record("h2.13", f"view replace (CREATE OR REPLACE VIEW as pub_c): n={len(VIEW_MS)}", None,
               f"median {statistics.median(VIEW_MS):.1f} ms, max {max(VIEW_MS):.1f} ms")

    # Control, with the reader stopped: the wrong order (tag first) on a dropped column errors; the reconcile repairs it.
    CONTRACT["t2"].remove("total")
    h2_rebuild("t2")
    meta = LIVE.table(CB, "t2")
    st, _ = LIVE.commit([rc.tag_change(CB, "t2", rc.ref_id(meta, "main"), AUDITED["t2"])])
    _, e_wrong, _ = tq(FULL, f"SELECT count(*) FROM s01.{C}.t2")
    fixed = h2_reconcile()
    rows, e_after, _ = tq(FULL, f"SELECT count(*) FROM s01.{C}.t2")
    record("h2.14", "control: tag first, then view, on a dropped column breaks reads (the rule is load-bearing); the "
           "reconcile repairs it", st in (200, 204) and e_wrong is not None and e_after is None,
           f"tag commit HTTP {st}; read {short(e_wrong)}; reconcile {fixed}; read after {short(e_after)} {rows}")


# ---- h3: the catalog fence ---------------------------------------------------------------------------------

def h3() -> None:
    fc = f"{FL}/catalog"
    ports = pl.docker("port", f"{P}-lakekeeper").strip()
    nets = pl.docker("inspect", f"{P}-lakekeeper", "--format",
                     "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}").split()
    ip = pl.docker("inspect", f"{P}-lakekeeper", "--format",
                   "{{(index .NetworkSettings.Networks \"" + NET_FENCE + "\").IPAddress}}").strip()
    record("h3.01", "the throwaway Lakekeeper (the stack's pin) is on its own network only, with no host port",
           nets == [NET_FENCE] and ports == "", f"networks {nets}; published ports {ports or 'none'}; {pl.LAKEKEEPER_IMAGE}")
    for ns in (A, AB, B, BB):
        ok(SVC, f"CREATE SCHEMA fence_build.{ns}")
    for ns, rows, who in ((AB, A_TESTED, "dbt_a"), (BB, B_TESTED, "dbt_b")):
        ok(who, f"CREATE TABLE fence_build.{ns}.t ({DDL})")
        ok(who, insert(f"fence_build.{ns}.t", rows))
    tags = {f"{ns}.t": main_id("fence_build", ns, "t", who) for ns, who in ((AB, "dbt_a"), (BB, "dbt_b"))}
    for key, sid in tags.items():
        code, out = tools("commit", fc, "fence", json.dumps([rc.tag_change(key.split(".")[0], "t", sid, None)]))
        assert code == 0, out
    for fence in (A, B):
        publish_view("fence", fence, "t", COLS)
    ok("dbt_a", insert(f"fence_build.{AB}.t", A_UNTESTED))
    untested = main_id("fence_build", AB, "t", "dbt_a")
    release = {"readerNamespaces": {f: {"t": {"owner": f"pub_{f.removeprefix('spike_s01_')}",
                                              "sql": view_sql("fence", f, "t", COLS)}} for f in (A, B)},
               "tags": tags}
    (BASE / "work" / "release.json").write_text(json.dumps(release, indent=1))
    (BASE / "work" / "release.json").chmod(0o644)
    b_rows = ok(FULL, f"SELECT id FROM fence.{B}.t ORDER BY id")
    forged_sql = f"SELECT {', '.join(COLS)} FROM fence.{AB}.t"  # A's main: untested, unmasked

    # From k2-net, where third-party images run: by name and by address.
    _, probes = from_k2net("probe", f"{fc}/v1/config?warehouse=fence", f"http://{ip}:8181/catalog/v1/config?warehouse=fence")
    record("h3.02", "from a container on k2-net the catalog API is unreachable, by name and by address",
           probes.count("unreachable") == 2, probes)
    cf, of = from_k2net("forge-view", f"http://{ip}:8181/catalog", "fence", B, "t", "pub_a", forged_sql)
    cg, og = from_k2net("graft", f"http://{ip}:8181/catalog", "fence", AB, "t", str(untested), BB, "t", "audited")
    code, out = tools("drift", fc, "fence", "/work/release.json")
    record("h3.03", "from k2-net both forgeries fail to connect, and the catalog is unchanged (drift check clean, exit 0)",
           cf == 2 and cg == 2 and code == 0, f"forged view: exit {cf} {of}; graft: exit {cg} {og}; drift: exit {code} {out}")

    # From inside the catalog network: both forgeries are accepted, readers are served, and the drift check fires.
    cf, of = tools("forge-view", fc, "fence", B, "t", "pub_a", forged_sql)
    rows = ok(FULL, f"SELECT id, secret FROM fence.{B}.t ORDER BY id")
    record("h3.04", "inside the catalog network a view POSTed with a forged owner (pub_a) replaces B's view: B's "
           "reader gets A's untested row and A's secrets", cf == 0 and [4, "zz-sec-4"] in rows,
           f"exit {cf} {of}; B's reader before {b_rows}, now {rows}")
    code, out = tools("drift", fc, "fence", "/work/release.json")
    record("h3.05", "the drift check then exits non-zero and names the planted view", code == 1 and f"{B}.t:" in out,
           f"exit {code}: {out}")
    publish_view("fence", B, "t", COLS)
    code, out = tools("drift", fc, "fence", "/work/release.json")
    record("h3.06", "republishing B's view from the release (the reconcile) makes it clean again", code == 0,
           f"exit {code}: {out}")
    cg, og = tools("graft", fc, "fence", AB, "t", str(untested), BB, "t", "audited")
    rows = ok(FULL, f"SELECT id, secret FROM fence.{B}.t ORDER BY id")
    record("h3.07", "inside, add-snapshot plus set-snapshot-ref grafts A's untested snapshot into B's base as its "
           "audited tag: B's own view now serves A's untested row", cg == 0 and [4, "zz-sec-4"] in rows,
           f"exit {cg} {og}; B's reader now {rows}")
    code, out = tools("drift", fc, "fence", "/work/release.json")
    record("h3.08", "the drift check then exits non-zero and names the moved tag", code == 1 and f"{BB}.t audited" in out,
           f"exit {code}: {out}")
    record("h3.09", "the host and the throwaway catalog's address", None, rc.probe(f"http://{ip}:8181/health", timeout=3))


# ---- run -----------------------------------------------------------------------------------------------------

def report(cleanup: list[str]) -> int:
    print("\n" + "=" * 100)
    print("spine-h-tested-data-view: results (FAIL = a leak or a pass criterion not met; INFO = recorded, not a criterion)")
    print("=" * 100)
    for rid, verdict, assertion, detail in RESULTS:
        print(f"{rid:6} {verdict:4}  {assertion}")
        print(f"{'':12}{detail[:400]}")
    for line in cleanup:
        print(f"cleanup: {line}")
    fails = [r[0] for r in RESULTS if r[1] == "FAIL"]
    print(f"\n{len(RESULTS)} rows: {sum(r[1] == 'PASS' for r in RESULTS)} PASS, {len(fails)} FAIL, "
          f"{sum(r[1] == 'INFO' for r in RESULTS)} INFO" + (f"; failed {fails}" if fails else ""))
    return 1 if fails else 0


def main() -> int:
    global BASE, LIVE
    pl.wait_memory()
    pl.load_root_credentials()
    BASE = Path(tempfile.mkdtemp(prefix="spike-s01.", dir=Path.home()))
    (BASE / "work").mkdir()
    for p in (BASE, BASE / "work"):
        p.chmod(0o755)  # the tools image runs as uid 185
    pl.log("leftovers:", "; ".join(clear_leftovers()[1]))
    t0 = time.monotonic()
    try:
        start_live_scratch()
        LIVE = rc.Catalog(f"{LK_LIVE}/catalog", WH)
        start_fence_lake()
        start_trino()
        for part in (h1, h2, h3):
            try:
                part()
            except Exception as e:  # noqa: BLE001
                record(f"{part.__name__}.x", f"{part.__name__} did not complete", False, f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        record("setup", "the stack for the gate started", False, f"{type(e).__name__}: {e}")
    finally:
        if POLLER:
            POLLER.stop.set()
        time.sleep(10)  # ponytail: a fixed wait for Lakekeeper's purge of dropped tables (3 to 7 s in spike b)
        clean, done = clear_leftovers()
        shutil.rmtree(BASE, ignore_errors=True)
    pl.log(f"run took {time.monotonic() - t0:.0f} s")
    if not clean:
        record("cleanup", "everything the spike made is removed", False, done)
    return report(done)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "publish-child":
        publish_child(sys.argv[2])
        sys.exit(0)
    sys.exit(main())
