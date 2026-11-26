"""N0-4 (b): Spark expire_snapshots by snapshot id on a table Trino wrote, with a scoped storage key.

Two phases, the same experiment in each:

  live      the live Lakekeeper, warehouse fx_reference (it holds no live namespace), as configured:
            its storage profile has remote signing on, the default lake-init leaves
  unsigned  a throwaway Lakekeeper v0.13.9 with its own Postgres, one warehouse on the scratch
            bucket spike-n04plat-b with `remote-signing-enabled: false`, so the client's key is used

A throwaway Trino 484 writes `<cat>.spike_n04plat_b.t`: two appends S1, S2, then seven orphans O0..O6,
each an append rolled back to S2 with `ALTER TABLE ... EXECUTE rollback_to_snapshot`, as the dbt
wrapper (A7 step 5) leaves a failed build. Five MinIO users, each with one policy on the table's
prefix, give five Spark catalogs in one throwaway Spark session from the live Spark image (512m driver),
and Spark expires one orphan per key by id. Trino then reads the table and MinIO says which files
are gone. Users, policies, tables, bucket and containers are removed afterwards. Synthetic rows only.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from platform_lib import (  # noqa: E402
    LABEL,
    LAKEKEEPER_IMAGE,
    NET,
    POSTGRES_IMAGE,
    SPARK_IMAGE,
    Trino,
    docker,
    iceberg_catalog,
    load_root_credentials,
    log,
    mc,
    redact,
    remove_containers,
    secret,
    wait_memory,
    write_trino_etc,
)

TC, SC, PG, LK = (f"spike-n04plat-b-{n}" for n in ("trino", "spark", "pg", "lakekeeper"))
NS, SVC = "spike_n04plat_b", "svc_spike"
BUCKET = "spike-n04plat-b"
CATS = ["p0", "p1", "p2", "p3", "p4"]
PHASES = {  # name: (trino catalog, warehouse, catalog URI on k2-net)
    "live": ("spikeb", "fx_reference", "http://lakekeeper:8181/catalog"),
    "unsigned": ("spikeu", "spike_n04plat_b", f"http://{LK}:8181/catalog"),
}
BASE = Path(tempfile.mkdtemp(prefix="spike-n04plat-b.", dir=Path.home()))


def policies(bucket: str, key_prefix: str) -> dict[str, list[dict]]:
    obj = f"arn:aws:s3:::{bucket}/{key_prefix}*"

    def allow(*actions: str) -> dict:
        return {"Effect": "Allow", "Action": [f"s3:{a}" for a in actions], "Resource": [obj]}

    return {
        "p0": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": ["arn:aws:s3:::spike-n04plat-none/*"]}],
        "p1": [allow("GetObject")],
        "p2": [allow("GetObject", "DeleteObject")],
        "p3": [allow("DeleteObject")],
        "p4": [allow("GetObject", "PutObject", "DeleteObject"),
               {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": [f"arn:aws:s3:::{bucket}"],
                "Condition": {"StringLike": {"s3:prefix": [f"{key_prefix}*"]}}}],
    }


def exists(paths: list[str]) -> dict[str, bool]:
    out = mc('while read -r p; do if mc stat "root/${p#s3://}" >/dev/null 2>&1; then echo "1 $p"; '
             'else echo "0 $p"; fi; done', stdin="\n".join(paths) + "\n")
    return {ln.split(" ", 1)[1]: ln.startswith("1") for ln in out.splitlines() if ln}


def delegation_config(uri: str, warehouse: str) -> dict:
    """What LoadTable hands a client, per X-Iceberg-Access-Delegation value (keys only, from k2-net)."""
    cfg = json.loads(mc(f'curl -fsS "{uri}/v1/config?warehouse={warehouse}"'))
    url = f"{uri}/v1/{cfg['defaults']['prefix']}/namespaces/{NS}/tables/t"
    out = {}
    for hdr in ("", "vended-credentials", "remote-signing"):
        h = f"-H 'X-Iceberg-Access-Delegation: {hdr}'" if hdr else ""
        c = json.loads(mc(f'curl -fsS {h} "{url}"')).get("config", {})
        out[hdr or "(no header)"] = c.get("s3.remote-signing-enabled", "absent")
    return out


def start_scratch_lakekeeper() -> None:
    """Postgres + Lakekeeper on k2-net, bootstrapped, one unsigned warehouse on the scratch bucket."""
    pw, key = secret(secrets.token_hex(16)), secret(secrets.token_hex(16))
    db = f"postgres://lk:{pw}@{PG}:5432/lakekeeper"
    os.environ.update({"POSTGRES_PASSWORD": pw, "LAKEKEEPER__PG_DATABASE_URL_READ": db,
                       "LAKEKEEPER__PG_DATABASE_URL_WRITE": db, "LAKEKEEPER__PG_ENCRYPTION_KEY": key})
    remove_containers(PG, LK)
    docker("run", "-d", "--name", PG, "--label", LABEL, "--network", NET, "--memory", "256m",
           "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_USER=lk", "-e", "POSTGRES_DB=lakekeeper", POSTGRES_IMAGE)
    for _ in range(60):
        if docker("exec", PG, "pg_isready", "-U", "lk", "-d", "lakekeeper", check=False).find("accepting") >= 0:
            break
        time.sleep(1)
    lk_env = ["-e", "LAKEKEEPER__PG_DATABASE_URL_READ", "-e", "LAKEKEEPER__PG_DATABASE_URL_WRITE",
              "-e", "LAKEKEEPER__PG_ENCRYPTION_KEY"]
    docker("run", "--rm", "--name", f"{LK}-migrate", "--label", LABEL, "--network", NET, "--memory", "256m",
           *lk_env, LAKEKEEPER_IMAGE, "migrate")
    docker("run", "-d", "--name", LK, "--label", LABEL, "--network", NET, "--memory", "256m", *lk_env,
           "-e", f"LAKEKEEPER__BASE_URI=http://{LK}:8181", "-e", "LAKEKEEPER__AUDIT__TRACING__ENABLED=false",
           LAKEKEEPER_IMAGE, "serve")
    for _ in range(60):
        if subprocess.run(["docker", "exec", LK, "/home/nonroot/lakekeeper", "healthcheck"],
                          capture_output=True).returncode == 0:
            break
        time.sleep(1)
    body = json.dumps({
        "warehouse-name": PHASES["unsigned"][1], "project-id": "00000000-0000-0000-0000-000000000000",
        "storage-profile": {"type": "s3", "bucket": BUCKET, "key-prefix": "wh", "endpoint": "http://minio:9000",
                            "region": "local-01", "path-style-access": True, "flavor": "s3-compat",
                            "sts-enabled": False, "remote-signing-enabled": False},
        "storage-credential": {"type": "s3", "credential-type": "access-key",
                               "aws-access-key-id": "@AK@", "aws-secret-access-key": "@SK@"}})
    # The root key reaches the request body inside the container, from its environment, never argv.
    out = mc(f'mc mb --ignore-existing root/{BUCKET} >/dev/null && '
             f'curl -fsS -o /dev/null -w "bootstrap %{{http_code}} " -X POST http://{LK}:8181/management/v1/bootstrap '
             """-H 'content-type: application/json' -d '{"accept-terms-of-use": true}' && """
             'body=$(cat); body=${body/@AK@/$MINIO_ROOT_USER}; body=${body/@SK@/$MINIO_ROOT_PASSWORD}; '
             'printf "%s" "$body" | '
             f'curl -fsS -o /dev/null -w "warehouse %{{http_code}}" -X POST http://{LK}:8181/management/v1/warehouse '
             "-H 'content-type: application/json' --data-binary @-", stdin=body)
    log("scratch lakekeeper:", out)


def run_phase(trino: Trino, phase: str) -> list[str]:
    cat, wh, uri = PHASES[phase]
    T = f"{cat}.{NS}.t"

    def q(sql: str) -> list:
        return trino.ok(SVC, sql)

    def last_snapshot() -> tuple[int, str]:
        r = q(f'SELECT snapshot_id, manifest_list FROM {cat}.{NS}."t$snapshots" ORDER BY committed_at DESC LIMIT 1')
        return r[0][0], r[0][1]

    def files() -> set[str]:
        return {r[0] for r in q(f'SELECT file_path FROM {cat}.{NS}."t$files"')}

    users = [f"spike-n04plat-b-{phase}-{c}" for c in CATS]
    try:
        q(f"CREATE SCHEMA {cat}.{NS}")
        q(f"CREATE TABLE {T} (id bigint, v varchar)")
        q(f"INSERT INTO {T} VALUES (1, 'a'), (2, 'b')")
        s1, s1_list = last_snapshot()
        s1_files = files()
        q(f"INSERT INTO {T} VALUES (3, 'c'), (4, 'd')")
        s2, _ = last_snapshot()
        s2_files = files()
        ddl = q(f"SHOW CREATE TABLE {T}")[0][0]
        m = re.search(r"location = 's3://([a-z0-9-]+)/([a-z0-9_/-]+)'", ddl)
        if not m:
            raise RuntimeError(f"unexpected location: {ddl[-200:]}")
        bucket, key_prefix = m.group(1), m.group(2) + "/"
        fmt = re.search(r"format_version = (\d+)", ddl)
        orphans = []
        for k in range(7):
            q(f"INSERT INTO {T} VALUES ({100 + k}, 'orphan {k}')")
            sid, mlist = last_snapshot()
            added = sorted(files() - s2_files)
            manifests = [r[0] for r in q(f'SELECT path FROM {cat}.{NS}."t$manifests" WHERE added_snapshot_id = {sid}')]
            q(f"ALTER TABLE {T} EXECUTE rollback_to_snapshot({s2})")
            orphans.append({"id": sid, "files": added, "manifests": manifests, "manifest_list": mlist})
        assert q(f"SELECT snapshot_id FROM {cat}.{NS}.\"t$refs\" WHERE name = 'main'")[0][0] == s2
        deleg = delegation_config(uri, wh)
        log(f"[{phase}] table by Trino: format_version {fmt.group(1) if fmt else '?'}, prefix s3://{bucket}/{key_prefix}, "
            f"S1 {s1}, S2 {s2} (main), 7 orphans; LoadTable s3.remote-signing-enabled by header: {deleg}")

        pols = policies(bucket, key_prefix)
        names, script = [], ""
        for c, user in zip(CATS, users):
            C = c.upper()
            # A random secret made for this run's scratch user, not a real credential; it still
            # reaches mc on stdin, not argv, so no process listing in the container shows it.
            os.environ[f"SPIKE_AK_{C}"], os.environ[f"SPIKE_SK_{C}"] = user, secret(secrets.token_hex(20))
            names += [f"SPIKE_AK_{C}", f"SPIKE_SK_{C}"]
            doc = json.dumps({"Version": "2012-10-17", "Statement": pols[c]})
            script += (f"printf '%s' '{doc}' > /tmp/{c}.json && mc admin policy create root {user} /tmp/{c}.json >/dev/null && "
                       f'printf "%s\\n" "$SPIKE_SK_{C}" | mc admin user add root "$SPIKE_AK_{C}" >/dev/null && '
                       f'mc admin policy attach root {user} --user "$SPIKE_AK_{C}" >/dev/null && ')
        made = mc(script + "mc admin user list root --json", extra_env=tuple(names))  # the table form truncates names
        log(f"[{phase}] scoped users created: {sum(u in made for u in users)} of {len(users)}")

        steps = [{"name": f"{c} {'+'.join(a.split(':')[1] for s in pols[c] for a in s['Action']) if c != 'p0' else 'none'}"
                          f": O{i} by id", "kind": "expire", "catalog": c, "id": orphans[i]["id"]}
                 for i, c in enumerate(CATS)]
        steps += [
            {"name": "p4: the current snapshot S2 by id", "kind": "expire", "catalog": "p4", "id": s2},
            {"name": "p4: history.expire.max-snapshot-age-ms = 1", "kind": "max_age_1ms", "catalog": "p4"},
            {"name": "p4: O5 by id, older_than epoch, max age 1 ms", "kind": "expire", "catalog": "p4",
             "id": orphans[5]["id"], "older_than_epoch": True},
            {"name": "p4: O6 by id only, max age 1 ms", "kind": "expire", "catalog": "p4", "id": orphans[6]["id"]},
        ]
        plan = {"namespace": NS, "table": "t", "warehouse": wh, "uri": uri, "catalogs": CATS, "steps": steps}
        wait_memory()
        remove_containers(SC)
        t0 = time.monotonic()
        p = subprocess.run(["docker", "run", "--rm", "-i", "--name", SC, "--label", LABEL, "--network", NET,
                            "--memory", "1536m", "--cpus", "1", *[a for n in names for a in ("-e", n)],
                            "--entrypoint", "python3", SPARK_IMAGE, "-", json.dumps(plan)],
                           input=(Path(__file__).parent / "expire.py").read_text(), capture_output=True, text=True)
        spark_s = time.monotonic() - t0
        leaked = sum(s in p.stdout + p.stderr for s in [os.environ["MINIO_ROOT_PASSWORD"]] +
                     [os.environ[f"SPIKE_SK_{c.upper()}"] for c in CATS])
        results = [json.loads(ln[7:]) for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
        if len(results) != len(steps):
            raise RuntimeError(f"spark exit {p.returncode}: " + redact(p.stderr[-1500:]))

        trino_snaps = {r[0] for r in q(f'SELECT snapshot_id FROM {cat}.{NS}."t$snapshots"')}
        rows = q(f"SELECT count(*), sum(id) FROM {T}")[0]
        main_after = q(f"SELECT snapshot_id FROM {cat}.{NS}.\"t$refs\" WHERE name = 'main'")[0][0]
        unreadable = []
        for o in orphans:
            _, err = trino.q(SVC, f"SELECT count(*) FROM {T} FOR VERSION AS OF {o['id']}")
            if err:
                unreadable.append(o["id"])
        paths = [x for o in orphans for x in o["files"] + o["manifests"] + [o["manifest_list"]]]
        there = exists(paths + sorted(s1_files) + [s1_list])

        def left(o: dict) -> str:
            f, mf = sum(there[x] for x in o["files"]), sum(there[x] for x in o["manifests"])
            return (f"files left: data {f}/{len(o['files'])}, manifest {mf}/{len(o['manifests'])}, "
                    f"manifest list {int(there[o['manifest_list']])}/1")

        def outcome(r: dict) -> str:
            if not r["ok"]:
                return "FAILED " + redact(r["error"])[:160]
            c = r["rows"][0] if r["rows"] else {}
            return (f"ok (deleted data {c.get('deleted_data_files_count')}, manifests {c.get('deleted_manifest_files_count')},"
                    f" lists {c.get('deleted_manifest_lists_count')}, stats {c.get('deleted_statistics_files_count')})")

        out = [f"[{phase}] warehouse {wh}, LoadTable s3.remote-signing-enabled: {deleg['(no header)']}; "
               f"Spark session {spark_s:.0f} s for {len(steps)} steps; secrets in Spark output: {leaked}"]
        for r, o in zip(results[:5], orphans[:5]):
            out.append(f"  {r['step']['name']}: {outcome(r)}; snapshot gone: {o['id'] not in r['snapshots']}; {left(o)}")
        out.append(f"  {results[5]['step']['name']}: {outcome(results[5])}; S2 kept: {s2 in results[5]['snapshots']}")
        out.append(f"  {results[6]['step']['name']}: {outcome(results[6])}")
        prev = set(results[6]["snapshots"])
        for r, o in zip(results[7:], orphans[5:]):
            others = sorted(prev - set(r["snapshots"]) - {o["id"]})
            out.append(f"  {r['step']['name']}: {outcome(r)}; named gone: {o['id'] not in r['snapshots']}; "
                       f"other snapshots expired by the same call: {len(others)}{' incl. S1' if s1 in others else ''}; "
                       f"{left(o)}")
            prev = set(r["snapshots"])
        out.append(f"  after: S1 data files kept (S2 reads them) {sum(there[x] for x in s1_files)}/{len(s1_files)}; "
                   f"Trino main = S2 {main_after == s2}, rows {rows} (expected [4, 10]), {len(trino_snaps)} snapshot(s) "
                   f"listed; time travel refused for {len(unreadable)} of 7 orphans "
                   f"(expired in metadata: {sum(o['id'] not in trino_snaps for o in orphans)})")
        for line in out:
            log(line)
        return out
    finally:
        trino.q(SVC, f"DROP TABLE IF EXISTS {T}")
        trino.q(SVC, f"DROP SCHEMA IF EXISTS {cat}.{NS}")
        if phase == "live" and "key_prefix" in locals():
            # Lakekeeper purges a dropped table's files asynchronously; wait for it, then make sure.
            out = mc(f'for i in $(seq 60); do [ "$(mc ls -r root/{bucket}/{key_prefix} | wc -l)" = 0 ] && break; '
                     f'sleep 1; done; echo "purged_after_s=$i"; mc rm -r --force root/{bucket}/{key_prefix} >/dev/null 2>&1; '
                     f'echo "left=$(mc ls -r root/{bucket}/{key_prefix} | wc -l)"')
            log(f"[{phase}] dropped table's prefix: {' '.join(out.split())}")
        script = "".join(f"mc admin user rm root {u} >/dev/null 2>&1; mc admin policy rm root {u} >/dev/null 2>&1; "
                         for u in users)
        rest = mc(script + "echo ===; mc admin user list root --json; echo ===; mc admin policy list root").split("===")
        log(f"[{phase}] cleanup: spike users left {sum(u in rest[1] for u in users)}, "
            f"policies left {sum(u in rest[2] for u in users)}")


def main() -> None:
    wait_memory()
    load_root_credentials()
    findings: list[str] = []
    trino = None
    try:
        start_scratch_lakekeeper()
        etc = BASE / "etc"
        write_trino_etc(etc, {cat: iceberg_catalog(wh, uri) for cat, wh, uri in PHASES.values()})
        trino = Trino(TC, etc, network=NET, env_names=("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"))
        log(f"trino up in {trino.start_s:.0f} s")
        for phase in PHASES:
            findings += run_phase(trino, phase)
    finally:
        remove_containers(TC, SC, LK, PG)
        out = mc(f'mc rb --force root/{BUCKET} >/dev/null 2>&1; mc ls root/{BUCKET} >/dev/null 2>&1 && echo present || echo gone; '
                 f'mc ls -r root/k2-lake/warehouse/fx_reference/ | wc -l')
        log(f"cleanup: scratch bucket {out.split()[0]}, objects under warehouse/fx_reference {out.split()[1]}")
        shutil.rmtree(BASE, ignore_errors=True)
    log("")
    log(f"FINDING (b): Spark ({SPARK_IMAGE}) expire_snapshots by id on a Trino 484-written table, scoped keys")
    for line in findings:
        log("  " + line)


if __name__ == "__main__":
    main()
