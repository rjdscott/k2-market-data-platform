"""N0-4 (e): can file-based rules fence writer A out of writer B's table in one writable catalog?

A throwaway Trino 484 on k2-net with three Iceberg catalogs through the live Lakekeeper:
`spikew` and `a_build` over warehouse market_capture, `b_build` over warehouse fx_reference (both
warehouses hold no live namespace). Scratch schemas `spike_n04plat_ea` (writer A's) and
`spike_n04plat_eb` (writer B's), synthetic rows only. Rules are swapped through a directory mount
with a 1 s refresh period (written from inside the container), in phases:

  R1   one shared catalog `spikew`: A owns ea, B owns eb (schema owner + table privileges),
       CALL procedures for the platform user only, retention session properties denied to writers
  R1b  as R1, and writers may EXECUTE the catalog's `system.*` procedures
  R2   one writer catalog per product: A reaches only `a_build`, B only `b_build`
       (through `b_build` B's eb lives in fx_reference; through `a_build` the R1 eb is still in reach)
  R4   `CREATE CATALOG` at run time (catalog.management=dynamic), the escape hatch's cost

Each statement runs as A against B's table and as A against A's own; a fingerprint of the target
(main snapshot, rows, columns, comment) read by the platform user before and after says whether
it changed. Everything is dropped and the scratch prefixes removed afterwards.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_lib import (  # noqa: E402
    NET,
    Trino,
    docker,
    iceberg_catalog,
    load_root_credentials,
    log,
    mc,
    remove_containers,
    wait_memory,
    write_trino_etc,
)

TC = "spike-n04plat-e-trino"
SVC, A, B, READER = "svc_spike", "dbt_spike_a", "dbt_spike_b", "p_reader"
EA, EB = "spike_n04plat_ea", "spike_n04plat_eb"
ALL = ["SELECT", "INSERT", "DELETE", "UPDATE", "OWNERSHIP", "GRANT_SELECT"]
RETENTION = "expire_snapshots_min_retention|remove_orphan_files_min_retention"
BASE = Path(tempfile.mkdtemp(prefix="spike-n04plat-e.", dir=Path.home()))
RULES = BASE / "rules"

META = ["snapshots", "files", "history", "manifests", "all_manifests", "partitions", "refs", "properties",
        "metadata_log_entries", "entries", "all_entries"]
# (key, sql, session). {t} = cat.sch.tbl; {own} = the actor's own schema in the same catalog.
STATEMENTS = [
    ("select", "SELECT count(*) FROM {t}", None),
    ("time_travel", "SELECT count(*) FROM {t} FOR VERSION AS OF {snap}", None),
    *[(f"meta_{m}", f'SELECT count(*) FROM {{cat}}.{{sch}}."{{tbl}}${m}"', None) for m in META],
    ("show_create", "SHOW CREATE TABLE {t}", None),
    ("describe", "DESCRIBE {t}", None),
    ("show_stats", "SHOW STATS FOR {t}", None),
    ("insert", "INSERT INTO {t} VALUES (100, 'x')", None),
    ("delete", "DELETE FROM {t} WHERE id = 1", None),
    ("update", "UPDATE {t} SET v = 'u' WHERE id = 2", None),
    ("merge", "MERGE INTO {t} d USING (VALUES (BIGINT '3', 'm')) s(id, v) ON d.id = s.id "
              "WHEN MATCHED THEN UPDATE SET v = s.v WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.v)", None),
    ("add_column", "ALTER TABLE {t} ADD COLUMN c2 varchar", None),
    ("set_properties", "ALTER TABLE {t} SET PROPERTIES sorted_by = ARRAY['id']", None),
    ("comment", "COMMENT ON TABLE {t} IS 'spike'", None),
    ("analyze", "ANALYZE {t}", None),
    ("create_table_in_schema", "CREATE TABLE {cat}.{sch}.t_extra (id bigint)", None),
    ("create_view_over", "CREATE VIEW {cat}.{own}.v_over AS SELECT * FROM {t}", None),
    ("optimize", "ALTER TABLE {t} EXECUTE optimize", None),
    ("optimize_manifests", "ALTER TABLE {t} EXECUTE optimize_manifests", None),
    ("expire_snapshots_7d", "ALTER TABLE {t} EXECUTE expire_snapshots(retention_threshold => '7d')", None),
    ("expire_snapshots_0s_session", "ALTER TABLE {t} EXECUTE expire_snapshots(retention_threshold => '0s')",
     "{cat}.expire_snapshots_min_retention=0s"),
    ("remove_orphan_files_7d", "ALTER TABLE {t} EXECUTE remove_orphan_files(retention_threshold => '7d')", None),
    ("drop_extended_stats", "ALTER TABLE {t} EXECUTE drop_extended_stats", None),
    ("rollback_to_snapshot", "ALTER TABLE {t} EXECUTE rollback_to_snapshot({snap})", None),
    ("call_rollback_to_snapshot", "CALL {cat}.system.rollback_to_snapshot('{sch}', '{tbl}', {snap})", None),
    ("call_unregister_table", "CALL {cat}.system.unregister_table('{sch}', '{tbl}')", None),
    ("truncate", "TRUNCATE TABLE {t}", None),
    ("create_or_replace", "CREATE OR REPLACE TABLE {t} AS SELECT BIGINT '9' AS id, 'r' AS v", None),
    ("rename", "ALTER TABLE {t} RENAME TO {cat}.{sch}.{tbl}_moved", None),
    ("drop_table", "DROP TABLE {t}", None),
    ("drop_schema_cascade", "DROP SCHEMA {cat}.{sch} CASCADE", None),
]
PROCS = {"optimize", "optimize_manifests", "expire_snapshots_7d", "expire_snapshots_0s_session",
         "remove_orphan_files_7d", "drop_extended_stats", "rollback_to_snapshot"}
CALLS = {"call_rollback_to_snapshot", "call_unregister_table"}


def rules(phase: str) -> dict:
    # Only this phase's marker user reaches catalog spikew, so set_rules() knows when the file is live.
    marker = {"user": f"u_phase_{phase}", "catalog": "spikew", "allow": "read-only"}
    session = [{"user": SVC, "allow": True}, {"property": RETENTION, "allow": False}, {"allow": True}]
    if phase in ("R1", "R1b", "R1s"):
        writers = "|".join([A, B]) if phase == "R1b" else "none_"
        return {
            "catalogs": [marker, {"user": SVC, "allow": "owner"},
                         {"user": f"{A}|{B}", "catalog": "spikew", "allow": "all"},
                         {"user": READER, "catalog": "spikew", "allow": "read-only"},
                         {"catalog": "system", "allow": "read-only"}],
            "schemas": [{"user": SVC, "owner": True},
                        {"user": A, "schema": EA, "owner": True},
                        {"user": B, "schema": EB, "owner": True}],
            "tables": [{"user": SVC, "privileges": ALL},
                       {"user": A, "schema": EA, "privileges": ALL},
                       # R1s: A may read B's table in the writable catalog, as an input read through it
                       *([{"user": A, "schema": EB, "table": "t", "privileges": ["SELECT"]}] if phase == "R1s" else []),
                       {"user": B, "schema": EB, "privileges": ALL},
                       {"user": READER, "schema": EB, "table": "t", "privileges": ["SELECT"]}],
            "procedures": [{"user": SVC, "privileges": ["EXECUTE"]},
                           {"user": writers, "schema": "system", "procedure": ".*", "privileges": ["EXECUTE"]}],
            "catalog_session_properties": session,
        }
    return {  # R2: one writer catalog per product
        "catalogs": [marker, {"user": SVC, "allow": "owner"},
                     {"user": A, "catalog": "a_build", "allow": "all"},
                     {"user": B, "catalog": "b_build", "allow": "all"},
                     {"catalog": "system", "allow": "read-only"}],
        "schemas": [{"user": SVC, "owner": True},
                    {"user": A, "catalog": "a_build", "schema": EA, "owner": True},
                    {"user": B, "catalog": "b_build", "schema": EB, "owner": True}],
        "tables": [{"user": SVC, "privileges": ALL},
                   {"user": A, "catalog": "a_build", "schema": EA, "privileges": ALL},
                   {"user": B, "catalog": "b_build", "schema": EB, "privileges": ALL}],
        "procedures": [{"user": SVC, "privileges": ["EXECUTE"]}],
        "catalog_session_properties": session,
    }


class Fence:
    def __init__(self, trino: Trino):
        self.t = trino
        self.locations: set[str] = set()

    def set_rules(self, phase: str) -> None:
        # Written from inside the container (the VM side of Docker Desktop's file sharing): a rename
        # done on the host is not seen through the directory mount by path lookups (finding (c)).
        docker("exec", "-i", TC, "bash", "-c", "cat > /spike-rules/.rules.tmp && "
               "mv -T /spike-rules/.rules.tmp /spike-rules/rules.json", input=json.dumps(rules(phase), indent=1))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            rows, err = self.t.q(f"u_phase_{phase}", "SHOW CATALOGS")
            if not err and ["spikew"] in rows:
                log(f"rules {phase} live after {time.monotonic() - t0:.1f} s")
                return
            time.sleep(0.5)
        raise RuntimeError(f"rules {phase} never applied")

    def make_table(self, cat: str, sch: str, owner: str = SVC) -> int:
        """A fresh two-insert table `t`; returns the first append's snapshot id."""
        q = self.t
        q.q(SVC, f"CREATE SCHEMA IF NOT EXISTS {cat}.{sch}")
        q.q(SVC, f"DROP TABLE IF EXISTS {cat}.{sch}.t")
        q.ok(owner, f"CREATE TABLE {cat}.{sch}.t (id bigint, v varchar)")
        q.ok(owner, f"INSERT INTO {cat}.{sch}.t VALUES (1, 'a'), (2, 'b')")
        q.ok(owner, f"INSERT INTO {cat}.{sch}.t VALUES (3, 'c'), (4, 'd')")
        ddl = q.ok(SVC, f"SHOW CREATE TABLE {cat}.{sch}.t")[0][0]
        # Lakekeeper puts each table at warehouse/<warehouse>/<table uuid>, no namespace segment.
        m = re.search(r"location = '(s3://k2-lake/warehouse/(market_capture|fx_reference)/[0-9a-f-]{36})'", ddl)
        if not m:
            raise RuntimeError(f"unexpected table location in {ddl[-300:]}")
        self.locations.add(m.group(1) + "/")
        return q.ok(SVC, f"SELECT snapshot_id FROM {cat}.{sch}.\"t$snapshots\" WHERE operation = 'append' "
                         f"ORDER BY committed_at LIMIT 1")[0][0]

    def fingerprint(self, cat: str, sch: str) -> dict:
        q = self.t
        rows, err = q.q(SVC, f"SELECT snapshot_id FROM {cat}.{sch}.\"t$refs\" WHERE name = 'main'")
        if err:
            return {"exists": False}
        return {"main": rows,
                "snapshots": q.ok(SVC, f'SELECT count(*) FROM {cat}.{sch}."t$snapshots"'),
                "rows": q.ok(SVC, f"SELECT count(*), coalesce(sum(id), 0) FROM {cat}.{sch}.t"),
                "cols": q.ok(SVC, f"SELECT count(*) FROM {cat}.information_schema.columns "
                                  f"WHERE table_schema = '{sch}' AND table_name = 't'"),
                "comment": q.ok(SVC, f"SELECT comment FROM system.metadata.table_comments WHERE catalog_name = "
                                     f"'{cat}' AND schema_name = '{sch}' AND table_name = 't'")}

    def tidy(self, cat: str, sch: str, own: str) -> None:
        for sql in (f"DROP TABLE IF EXISTS {cat}.{sch}.t_extra", f"DROP VIEW IF EXISTS {cat}.{own}.v_over",
                    f"DROP TABLE IF EXISTS {cat}.{sch}.t_moved"):
            self.t.q(SVC, sql)

    def matrix(self, label: str, user: str, cat: str, sch: str, own: str, only: set[str] | None = None) -> dict:
        snap = self.make_table(cat, sch)
        out = {}
        for key, sql, session in STATEMENTS:
            if only and key not in only:
                continue
            before = self.fingerprint(cat, sch)
            if not before.get("exists", True):
                snap = self.make_table(cat, sch)
                before = self.fingerprint(cat, sch)
            f = {"t": f"{cat}.{sch}.t", "cat": cat, "sch": sch, "tbl": "t", "own": own, "snap": snap}
            rows, err = self.t.q(user, sql.format(**f), session.format(**f) if session else None)
            after = self.fingerprint(cat, sch)
            changed = after != before
            if err is None:
                res = "ok"
            elif err.startswith("PERMISSION_DENIED") or "Access Denied" in err:
                res = "denied: " + err.split("Access Denied: ", 1)[-1][:90]
            else:
                res = "error " + err[:110]
            out[key] = res + (" CHANGED" if changed else "")
            self.tidy(cat, sch, own)
            if changed:
                snap = self.make_table(cat, sch)
        log(f"{label}: " + json.dumps(out))
        return out


def summarise(m: dict) -> str:
    ok = [k for k, v in m.items() if v.startswith("ok")]
    changed = [k for k, v in m.items() if "CHANGED" in v]
    denied = [k for k, v in m.items() if v.startswith("denied")]
    other = {k: v for k, v in m.items() if v.startswith("error")}
    return (f"{len(m)} statements: ran {len(ok)} {ok}; changed the target {len(changed)} {changed}; "
            f"denied {len(denied)}; other errors {len(other)} {other}")


def main() -> None:
    wait_memory()
    load_root_credentials()
    RULES.mkdir()
    RULES.chmod(0o777)  # the Trino image runs as uid 1000 and rewrites the file between phases
    (RULES / "rules.json").write_text(json.dumps(rules("R1")))
    (RULES / "rules.json").chmod(0o644)
    etc = BASE / "etc"
    write_trino_etc(etc, {"spikew": iceberg_catalog("market_capture"), "a_build": iceberg_catalog("market_capture"),
                          "b_build": iceberg_catalog("fx_reference")},
                    extra_files={"access-control.properties": "access-control.name=file\n"
                                 "security.config-file=/spike-rules/rules.json\nsecurity.refresh-period=1s\n"},
                    config_extra="catalog.management=dynamic\n")
    res: dict = {}
    trino = fence = None
    try:
        trino = Trino(TC, etc, network=NET, mounts=(f"{RULES}:/spike-rules",),
                      env_names=("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"), etc_ro=False)
        log(f"trino up in {trino.start_s:.0f} s")
        fence = Fence(trino)
        fence.set_rules("R1")
        trino.ok(SVC, f"CREATE SCHEMA spikew.{EA}")
        trino.ok(SVC, f"CREATE SCHEMA spikew.{EB}")
        fence.make_table("spikew", EB, owner=B)  # each writer creates its own table under R1
        fence.make_table("spikew", EA, owner=A)
        log("table location prefixes:", sorted(fence.locations))

        res["R1 A on B"] = fence.matrix("R1 A on B (shared catalog)", A, "spikew", EB, EA)
        res["R1 A on A"] = fence.matrix("R1 A on A (control)", A, "spikew", EA, EA)
        res["R1 platform"] = fence.matrix("R1 platform user, retention floor lowered for its session", SVC,
                                          "spikew", EB, EB, only={"expire_snapshots_0s_session"})
        res["R1 reader"] = fence.matrix("R1 reader with read-only catalog access and SELECT on B", READER,
                                        "spikew", EB, EB, only={"select", "insert", *PROCS, "drop_table"})
        fence.set_rules("R1b")
        res["R1b A on B"] = fence.matrix("R1b A on B, writers may CALL", A, "spikew", EB, EA, only=CALLS)
        res["R1b A on A"] = fence.matrix("R1b A on A, writers may CALL", A, "spikew", EA, EA, only=CALLS)
        fence.set_rules("R1s")
        res["R1s A on B"] = fence.matrix("R1s A on B, A granted SELECT on B's table", A, "spikew", EB, EA,
                                         only={"select", "meta_snapshots", "insert", *PROCS, "drop_table"})

        fence.set_rules("R2")
        trino.ok(SVC, f"CREATE SCHEMA b_build.{EB}")
        fence.make_table("b_build", EB, owner=B)
        res["R2 A on B via b_build"] = fence.matrix("R2 A on B through B's catalog", A, "b_build", EB, EA)
        res["R2 A on B via a_build"] = fence.matrix("R2 A on B's table in A's warehouse through A's catalog",
                                                    A, "a_build", EB, EA)
        res["R2 A on A"] = fence.matrix("R2 A on A (control)", A, "a_build", EA, EA)
        res["R2 B on B"] = fence.matrix("R2 B on B (control)", B, "b_build", EB, EB,
                                        only={"select", "insert", "optimize", "rollback_to_snapshot", "drop_table"})

        # R4: a new writer catalog at run time, without a restart.
        t0 = time.monotonic()
        props = ", ".join(f"\"{k}\" = '{v}'" for k, v in (ln.split("=", 1) for ln in
                          iceberg_catalog("market_capture").splitlines()[1:]))
        _, err = trino.q(SVC, f"CREATE CATALOG spike_c_build USING iceberg WITH ({props})")
        dyn = {"create": err or f"ok in {time.monotonic() - t0:.1f} s"}
        rows, err = trino.q(SVC, "SHOW SCHEMAS FROM spike_c_build")
        dyn["svc_show_schemas"] = err or sorted(r[0] for r in rows)
        _, err = trino.q(A, "SHOW SCHEMAS FROM spike_c_build")
        dyn["writer_a_show_schemas"] = "denied" if err and "Access Denied" in err else (err or "ok")
        stored = etc / "catalog" / "spike_c_build.properties"
        if stored.exists():  # how the catalog store keeps the key: names and value shapes only
            # java.util.Properties format: ':' is written as '\:'
            kv = dict(ln.replace("\\:", ":").split("=", 1) for ln in stored.read_text().splitlines()
                      if "=" in ln and not ln.startswith("#"))
            dyn["stored_s3_key_values"] = {
                k: f"reference {v}" if "ENV:MINIO_ROOT" in v else "the root secret in clear"
                if v == os.environ["MINIO_ROOT_PASSWORD"] else "the root user in clear"
                if v == os.environ["MINIO_ROOT_USER"] else f"other ({len(v)} chars)"
                for k, v in kv.items() if k.startswith("s3.aws")}
            dyn["stored_property_count"] = len(kv)
        else:
            dyn["stored_file"] = "absent"
        # Does the stored catalog come back after a restart?
        docker("restart", TC)
        trino.port = int(docker("port", TC, "8080/tcp").strip().splitlines()[0].rsplit(":", 1)[1])
        t1 = time.monotonic()
        while time.monotonic() - t1 < 180:
            try:
                rows, err = trino.q(SVC, "SHOW SCHEMAS FROM spike_c_build")
                if not (err or "").startswith("SERVER_STARTING_UP"):
                    break
            except OSError:
                pass
            time.sleep(2)
        dyn["after_restart_show_schemas"] = err or f"ok, {len(rows)} schemas"
        _, err = trino.q(SVC, "DROP CATALOG spike_c_build")
        dyn["drop"] = err or "ok"
        res["R4 dynamic catalog"] = dyn
        log("R4 dynamic catalog:", json.dumps(dyn))
    finally:
        if trino:
            cleanup(trino, fence.locations if fence else set())
        remove_containers(TC)
        shutil.rmtree(BASE, ignore_errors=True)
    report(res)


def cleanup(trino: Trino, created: set[str]) -> None:
    fence = Fence(trino)
    try:
        fence.set_rules("R2")  # the platform user has everything in every rule set
    except Exception as e:  # noqa: BLE001
        log("cleanup: rules switch failed:", e)
    locations: set[str] = set()
    for cat, sch in (("spikew", EA), ("spikew", EB), ("b_build", EB)):
        rows, err = trino.q(SVC, f"SHOW TABLES FROM {cat}.{sch}")
        for (tbl,) in rows if not err else []:
            ddl, e2 = trino.q(SVC, f"SHOW CREATE TABLE {cat}.{sch}.{tbl}")
            m = re.search(r"location = '(s3://k2-lake/warehouse/[a-z_]+/[0-9a-f-]{36})'", ddl[0][0]) if ddl else None
            if m:
                locations.add(m.group(1) + "/")
            trino.q(SVC, f"DROP TABLE IF EXISTS {cat}.{sch}.{tbl}")
        trino.q(SVC, f"DROP SCHEMA IF EXISTS {cat}.{sch}")
    left = [r for cat in ("spikew", "b_build") for r in trino.q(SVC, f"SHOW SCHEMAS FROM {cat} LIKE 'spike_n04plat%'")[0]]
    prefixes = sorted(locations | created)
    for p in prefixes:
        assert re.fullmatch(r"s3://k2-lake/warehouse/(market_capture|fx_reference)/[0-9a-f-]{36}/", p), p
    script = "".join(f'mc rm -r --force "root/{p[5:]}" >/dev/null 2>&1; n=$(mc ls -r "root/{p[5:]}" | wc -l); '
                     f'echo "{p} $n"; ' for p in prefixes)
    out = mc(script) if prefixes else ""
    log(f"cleanup: schemas left {left}; objects left per scratch prefix: {out.split()[1::2] or 'none'}")


def report(res: dict) -> None:
    log("")
    log("FINDING (e): one writable catalog shared by two writers under Trino 484 file-based rules")
    for k, m in res.items():
        if k.startswith("R4"):
            log(f"  {k}: {m}")
        else:
            log(f"  {k}: {summarise(m)}")


if __name__ == "__main__":
    main()
