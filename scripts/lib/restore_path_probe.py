"""scripts/verify/restore-path.sh's reads of the lake and its claims, in the throwaway lake's tools
container: /work is the lake's state, /work/rp the run's files, /work/backup the backup directory.

    restore_path_probe.py state <release id> <label>     the backup set's tables, rows, holds, landings to rp/restore-<label>.json
    restore_path_probe.py plant <release id> <table>     the table dropped without purge, a new one made at its name
    restore_path_probe.py where <table>                  the table's location as <bucket>/<prefix>/, for the script's wipe
    restore_path_probe.py claim <name> <release id> ...  one PASS or FAIL line; exit 1 on a FAIL

Only `plant` writes, the damage claim 13 needs; the restore itself is scripts/restore.py."""
import json
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path[:0] = ["/src/scripts", "/src/scripts/verify"]
import access_verify as av  # noqa: E402
import catalog_drift as cd  # noqa: E402

RP, BACKUP = Path("/work/rp"), Path("/work/backup")
trino, env = av.connect(Path("/work/env.json"))
LK = f"{env['lakekeeper']}/catalog"
# What each table's rows are read as: the product's builder, or svc.runs for the run records
ROWS = {"k2.ecb_fx_rates.bronze_daily": ("dbt_ecb_fx_rates", "lake.ecb_fx_rates.bronze_daily"),
        "governance.run_records.run_record": ("svc.runs", "governance_build.run_records.run_record"),  # k2runs.TABLE, as svc.runs reads it
        "fx_reference.ecb_fx_rates__base.gold_eur_rates_daily": ("p.analyst-a", "fx_reference.ecb_fx_rates.gold_eur_rates_daily")}
cmd, args = sys.argv[1], sys.argv[2:]


def backup_set(rid):
    return json.loads(Path(f"/work/releases/{rid}/compiled/lake/backup-set.json").read_text())


def load(table):
    wh, ns, t = table.split(".")
    st, doc = cd.call("GET", f"{cd.Catalog(LK, wh).base}/namespaces/{ns}/tables/{t}")
    return doc if st == 200 else None


def landing(prefix, pair):
    ns, out, token = "{http://s3.amazonaws.com/doc/2006-03-01/}", {}, ""
    while True:
        q = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
        st, code, body = av.s3(os.environ.get("K2_S3_ENDPOINT", av.S3_ENDPOINT), pair, "GET", "k2-landing", query=q)
        assert st == 200, f"listing k2-landing/{prefix}: {av.answered(st, code, body)}"
        doc = ET.fromstring(body)
        out |= {c.findtext(f"{ns}Key"): int(c.findtext(f"{ns}Size")) for c in doc.iter(f"{ns}Contents")}
        token = doc.findtext(f"{ns}NextContinuationToken") or ""
        if doc.findtext(f"{ns}IsTruncated") != "true" or not token:
            return out


def state(rid):
    out = {"tables": {}, "rows": {}, "landings": {}}
    for e in backup_set(rid)["entries"]:
        if e["kind"] == "table":
            d = load(e["table"])
            meta = (d or {}).get("metadata") or {}
            schema = next((s for s in meta.get("schemas", []) if s["schema-id"] == meta.get("current-schema-id")), {})
            out["tables"][e["table"]] = None if d is None else {
                "metadata": d["metadata-location"], "location": meta["location"], "snapshots": len(meta.get("snapshots") or []),
                "refs": {k: r["snapshot-id"] for k, r in (meta.get("refs") or {}).items()},
                "columns": [f["name"] for f in schema.get("fields", [])]}
        elif e["kind"] == "landing":
            out["landings"][e["prefix"]] = landing(e["prefix"], (os.environ["K2_BACKUP_S3_ACCESS_KEY"], os.environ["K2_BACKUP_S3_SECRET_KEY"]))
    for table, (user, rel) in ROWS.items():
        a = trino.run(user, f"SELECT count(*) FROM {rel}")
        out["rows"][table] = None if a.error else a.rows[0][0]
    a = trino.run("svc.runs", "SELECT product_id, kind, CAST(held_at AS varchar), CAST(released_at AS varchar) FROM governance.run_records.holds")
    out["holds"] = None if a.error else sorted(a.rows, key=lambda x: [str(v) for v in x])
    return out


def newest_manifest():
    runs = sorted(p for p in (BACKUP / "runs").iterdir() if p.is_dir() and not p.name.startswith("."))
    return json.loads((runs[-1] / "manifest.json").read_text())


def text(name):
    p = RP / name
    return p.read_text() if p.is_file() else ""


def claim(name, rid):
    s = {k: json.loads(text(f"restore-{k}.json") or "null") for k in ("built", "after", "again", "planted")}
    m = newest_manifest()
    dags = sorted(d["product"] for d in json.loads(Path(f"/work/releases/{rid}/compiled/airflow/dags.json").read_text())["dags"])
    out = text("drill.out")
    if name == "backup":
        verify = text("verify.out")
        checks = [("backup-verify: OK: 0 problem(s)" in verify, f"backup-verify: {verify.strip().splitlines()[-1:] or 'no output'}"),
                  (len(m["tables"]) >= 8 and m["dumps"].get("lakekeeper") and m.get("releases"), "the run lacks tables, the catalog dump or the releases tar")]
        what = f"{len(m['tables'])} restore tables, {len(m['rebuild'])} rebuild, {len(m['landings'])} landings, the catalog dump and the releases tar"
    elif name == "refused-run":
        older, partial = text("older.err"), text("partial.err")
        checks = [(text("older.rc").strip() == "2" and "older than the newest complete run" in older, f"an older run: exit {text('older.rc').strip()}: {older.strip()[-200:]}"),
                  (text("partial.rc").strip() == "2" and "is incomplete" in partial, f"an incomplete run: exit {text('partial.rc').strip()}: {partial.strip()[-200:]}"),
                  (s["built"]["holds"] == [], f"a hold was written: {s['built']['holds']}")]
        what = "a run older than the newest and an incomplete run are refused (exit 2) before anything is paused, held or written"
    elif name == "refused-unheld":
        err, gone = text("unheld.err"), json.loads(text("unheld-gone.json") or "{}")
        checks = [(text("unheld.rc").strip() == "2" and "not every product is held" in err, f"exit {text('unheld.rc').strip()}: {err.strip()[-200:]}"),
                  (gone.get("before") == gone.get("after") == 0, f"the wiped table's objects: {gone}")]
        what = "`lake --phase rest` with no product held is refused (exit 2) and puts nothing back"
    elif name == "drill":
        rc = text("drill.rc").strip()
        took = [x for x in out.splitlines() if x.startswith(("restore: OK in", "drill:"))]
        checks = [(rc == "0", f"restore.py drill exit {rc}: {text('drill.err').strip()[-300:]}"),
                  ("catalog database: replayed from" in out, "the catalog database was not replayed from its dump")]
        what = "the drill empties the catalog database, warehouse k2, warehouse governance and k2-landing, and restores: " + " | ".join(took)
    elif name == "restored":
        bad = []
        for t in m["tables"]:
            got = (s["after"]["tables"].get(t["table"]) or {})
            if t["table"] == "governance.run_records.holds":  # the restore's own holds commit to it after its register (claim 11)
                continue
            if got.get("metadata") != t["metadata_location"]:
                bad.append(f"{t['table']} at {got.get('metadata')}, the backup's {t['metadata_location']}")
        rows = {k: (s["built"]["rows"][k], s["after"]["rows"][k]) for k in ROWS}
        checks = [(not bad, "; ".join(bad[:3])),
                  (all(a == b and a for a, b in rows.values()), f"rows before and after: {rows}")]
        what = f"every restore table registered at the backup's metadata; rows as before the damage: {rows}"
    elif name == "landings":
        checks = [(s["after"]["landings"] == s["built"]["landings"] and any(s["built"]["landings"].values()),
                   f"after {s['after']['landings']}, before {s['built']['landings']}")]
        what = f"every landing object back: {sum(len(v) for v in s['after']['landings'].values())} object(s)"
    elif name == "rebuilt":
        k2 = [t for t in m["rebuild"] if t.startswith("k2.") and s["built"]["tables"].get(t)]
        bad = [t for t in k2 if f"rebuild {t}: its files are gone: dropped without purge, recreated empty" not in out
               or (s["after"]["tables"].get(t) or {}).get("columns") != s["built"]["tables"][t]["columns"]
               or (s["after"]["tables"].get(t) or {}).get("snapshots") != 0]
        kept = [t for t in m["rebuild"] if not t.startswith(("k2.", "governance.")) and s["built"]["tables"].get(t)]
        bad += [t for t in kept if f"rebuild {t}: whole: kept" not in out or s["after"]["tables"][t]["refs"] != s["built"]["tables"][t]["refs"]]
        checks = [(k2 and kept and not bad, f"wrong: {bad}; recreated {k2}; kept {kept}")]
        what = f"rebuild tables whose files went recreated empty with their columns ({', '.join(k2)}); the whole ones kept with their refs ({len(kept)})"
    elif name == "holds":
        rows = s["after"]["holds"] or []
        held = {p for p, k, _, r in rows if k == "restore" and r is None}
        freed = {p for p, k, _, r in rows if k == "restore" and r is not None}
        checks = [(held == freed == set(dags), f"held {sorted(held)}, released {sorted(freed)}, products {dags}"),
                  (out.find("held ") < out.find("table k2.") or "table k2." not in out, "a k2 table came back before the holds")]
        what = f"every product of dags.json ({len(dags)}) held (restore) before its tables came back, released after"
    elif name == "tags":
        d, rc = text("drift-runs.out"), text("drift-runs.rc").strip()
        compared = [x for x in d.splitlines() if x.startswith("SKIP tag")]
        checks = [(rc == "0" and "clean, the catalog equals the release" in d, f"catalog_drift.py --runs exit {rc}: {d.strip()[-300:]}"),
                  (not compared, f"tags not held to the run records: {compared[:2]}"),
                  ("the audited tags equal the run records" in out, "restore did not report the tags equal")]
        tagged = [t for t, v in s["after"]["tables"].items() if v and "audited" in v["refs"]]
        checks.append((tagged and all(s["after"]["tables"][t]["refs"] == s["built"]["tables"][t]["refs"] for t in tagged), f"tags {tagged}"))
        what = f"catalog_drift.py --runs clean: each audited tag ({len(tagged)}) equal to its newest audited run record, as before the damage"
    elif name == "again":
        a = text("again.out")
        moved = [x for x in a.splitlines() if x.strip().startswith(("table", "landing", "rebuild")) and
                 not (" 0 file(s) put back" in x or " 0 object(s) put back" in x or "whole: kept" in x or ": absent: " in x)]
        moved += [x for x in a.splitlines() if "registered at" in x or "re-pointed" in x or "recreated" in x]
        checks = [(text("again.rc").strip() == "0", f"exit {text('again.rc').strip()}: {text('again.err').strip()[-200:]}"),
                  (not moved, f"moved: {moved[:3]}"),
                  ({k: v for k, v in s["again"]["rows"].items()} == s["after"]["rows"], "rows changed")]
        what = "a second restore on the restored lake puts nothing back, registers, re-points and recreates nothing"
    elif name == "refused-lake":
        err = text("planted.err")
        checks = [(text("planted.rc").strip() == "2" and "not the ones the backup holds" in err, f"exit {text('planted.rc').strip()}: {err.strip()[-200:]}"),
                  (s["planted"]["holds"] == s["again"]["holds"], "the refused restore wrote a hold")]
        what = "a restore onto a lake whose restore table is another table (dropped, a new one made at its name) is refused (exit 2) before any hold or write"
    else:
        raise SystemExit(f"no claim {name}")
    failed = [why for ok, why in checks if not ok]
    print(f"{'FAIL' if failed else 'PASS'} {name}: {what}" + (f": {'; '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if cmd == "state":
    (RP / f"restore-{args[1]}.json").write_text(json.dumps(state(args[0]), indent=1, default=str))
elif cmd == "plant":
    import restore
    lake, table = restore.Lake(LK), args[1]
    meta = lake.load(table)["metadata"]
    lake.drop(table)
    lake.create(table, meta)
    print(f"planted {table}: a new table at {lake.load(table)['metadata']['location']}, the old at {meta['location']}")
elif cmd == "where":
    loc = urllib.parse.urlsplit(load(args[0])["metadata"]["location"])
    print(f"{loc.netloc}/{loc.path.strip('/')}/")
elif cmd == "claim":
    sys.exit(claim(args[0], args[1]))
