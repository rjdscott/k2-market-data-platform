"""Iceberg REST helpers for spine-h: tag commits, the drift check, the two forgeries, probes.

Stdlib only, Python 3.10 or later: spike.py imports it on the host (the live Lakekeeper's published
port), and it runs as a script inside a container on the throwaway catalog network, where there is
no published port at all:

  catalog.py bootstrap  <lakekeeper>
  catalog.py create-warehouse <lakekeeper> <name> <bucket> <s3-endpoint> <key-env> <secret-env>
  catalog.py commit     <catalog-uri> <warehouse> <changes-json>
  catalog.py forge-view <catalog-uri> <warehouse> <namespace> <view> <owner> <sql>
  catalog.py graft      <catalog-uri> <warehouse> <src-ns> <src-table> <snapshot-id> <dst-ns> <dst-table> <ref>
  catalog.py drift      <catalog-uri> <warehouse> <release-json-file>
  catalog.py probe      <url>...

<lakekeeper> is the server root (http://host:8181), <catalog-uri> its /catalog. Exit codes: 0 done
(drift: clean), 1 drift found or the request was refused, 2 the catalog could not be reached.
A storage key reaches a request body from the environment variable named on the command line,
never from argv, and no response body that could echo it is printed.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

PROJECT = "00000000-0000-0000-0000-000000000000"
OWNER_PROPERTY = "trino.run-as-owner"


class Unreachable(Exception):
    """No HTTP answer at all: refused, no route, timed out, or the name does not resolve."""


def call(method: str, url: str, body: object = None, timeout: float = 5) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        raise Unreachable(str(getattr(e, "reason", e))) from None


def error_text(doc: dict) -> str:
    e = doc.get("error") or {}
    return f"{e.get('type', '')}: {str(e.get('message', ''))[:160]}"


class Catalog:
    def __init__(self, uri: str, warehouse: str):
        self.uri = uri.rstrip("/")
        st, cfg = call("GET", f"{self.uri}/v1/config?warehouse={warehouse}")
        if st != 200:
            raise RuntimeError(f"config for warehouse {warehouse}: HTTP {st} {error_text(cfg)}")
        self.base = f"{self.uri}/v1/{cfg['defaults']['prefix']}"

    def _get(self, path: str) -> dict | None:
        st, doc = call("GET", f"{self.base}/{path}")
        return doc if st == 200 else None

    def namespaces(self) -> list[str]:
        return [ns[0] for ns in (self._get("namespaces") or {}).get("namespaces", [])]

    def tables(self, ns: str) -> list[str]:
        return [i["name"] for i in (self._get(f"namespaces/{ns}/tables") or {}).get("identifiers", [])]

    def views(self, ns: str) -> list[str]:
        return [i["name"] for i in (self._get(f"namespaces/{ns}/views") or {}).get("identifiers", [])]

    def table(self, ns: str, t: str) -> dict | None:
        doc = self._get(f"namespaces/{ns}/tables/{t}")
        return doc["metadata"] if doc else None

    def view(self, ns: str, v: str) -> dict | None:
        doc = self._get(f"namespaces/{ns}/views/{v}")
        return doc["metadata"] if doc else None

    def commit(self, changes: list[dict]) -> tuple[int, dict]:
        """One multi-table commit: every change applies, or none does."""
        return call("POST", f"{self.base}/transactions/commit", {"table-changes": changes})


def ref_id(meta: dict, ref: str) -> int | None:
    r = (meta.get("refs") or {}).get(ref)
    return r["snapshot-id"] if r else None


def schema_columns(meta: dict, snapshot_id: int | None = None) -> list[str]:
    """Column names of a snapshot's schema (the current schema when no snapshot is named)."""
    sid = meta["current-schema-id"]
    if snapshot_id is not None:
        sid = next(s for s in meta["snapshots"] if s["snapshot-id"] == snapshot_id).get("schema-id", sid)
    return [f["name"] for f in next(s for s in meta["schemas"] if s["schema-id"] == sid)["fields"]]


def tag_change(ns: str, table: str, tested: int, previous: int | None) -> dict:
    """One output's part of a product's tag commit (A7 step 6): `main` must still be the tested
    snapshot and `audited` the previous one (null before the first publish), else 409, nothing moves."""
    return {"identifier": {"namespace": [ns], "name": table},
            "requirements": [{"type": "assert-ref-snapshot-id", "ref": "main", "snapshot-id": tested},
                             {"type": "assert-ref-snapshot-id", "ref": "audited", "snapshot-id": previous}],
            "updates": [{"action": "set-snapshot-ref", "ref-name": "audited", "type": "tag", "snapshot-id": tested}]}


def view_state(meta: dict) -> dict:
    v = next(x for x in meta["versions"] if x["version-id"] == meta["current-version-id"])
    schema = next(s for s in meta["schemas"] if s["schema-id"] == v["schema-id"])
    return {"columns": [f["name"] for f in schema["fields"]],
            "owner": (meta.get("properties") or {}).get(OWNER_PROPERTY),
            "dialects": sorted(r.get("dialect", "") for r in v["representations"]),
            "sql": [r["sql"] for r in v["representations"] if r.get("dialect") == "trino"],
            "default-catalog": v.get("default-catalog"), "default-namespace": v.get("default-namespace")}


def norm_sql(sql: str) -> str:
    """Token-level form: Trino stores a view's SQL re-formatted (line breaks, spaces around commas)."""
    return " ".join(sql.replace(",", " , ").replace("(", " ( ").replace(")", " ) ").split())


# ---- the drift check (proposal condition 5) --------------------------------------------------------

def drift(cat: Catalog, release: dict) -> list[str]:
    """What differs from the release: reader namespaces hold exactly the generated views, each with
    the generated SQL, one trino representation and the publisher as owner; base namespaces hold no
    view; every `audited` tag is the snapshot the publish step recorded."""
    found = []
    expected = release["readerNamespaces"]
    present = cat.namespaces()
    for ns in present:
        if ns.endswith("__base"):
            for v in cat.views(ns):
                found.append(f"DRIFT view {ns}.{v}: a view in a base namespace")
        elif ns not in expected:
            found.append(f"DRIFT namespace {ns}: not in the release")
    for ns, views in expected.items():
        if ns not in present:
            found.append(f"DRIFT namespace {ns}: missing")
            continue
        for t in cat.tables(ns):
            found.append(f"DRIFT table {ns}.{t}: a table in a reader namespace")
        actual = set(cat.views(ns))
        for v in sorted(actual - set(views)):
            found.append(f"DRIFT view {ns}.{v}: not in the release")
        for v, want in views.items():
            meta = cat.view(ns, v)
            if meta is None:
                found.append(f"DRIFT view {ns}.{v}: missing")
                continue
            s = view_state(meta)
            if s["owner"] != want["owner"]:
                found.append(f"DRIFT view {ns}.{v}: owner {s['owner']!r}, release says {want['owner']!r}")
            if s["dialects"] != ["trino"]:
                found.append(f"DRIFT view {ns}.{v}: representations {s['dialects']}, release says ['trino']")
            if [norm_sql(x) for x in s["sql"]] != [norm_sql(want["sql"])]:
                found.append(f"DRIFT view {ns}.{v}: SQL {norm_sql(' | '.join(s['sql']))!r}, "
                             f"release says {norm_sql(want['sql'])!r}")
    for key, sid in release["tags"].items():
        ns, t = key.split(".")
        meta = cat.table(ns, t)
        got = ref_id(meta, "audited") if meta else None
        if got != sid:
            found.append(f"DRIFT tag {ns}.{t} audited: {got}, release records {sid}")
    return found


# ---- the two forgeries of the O8 review (B2) ---------------------------------------------------------

def forge_view(cat: Catalog, ns: str, name: str, owner: str, sql: str) -> list[int]:
    """Replace a view by plain REST calls: same name and columns, any SQL, any owner property."""
    meta = cat.view(ns, name)
    if meta is None:
        raise RuntimeError(f"no view {ns}.{name} to forge over")
    v = next(x for x in meta["versions"] if x["version-id"] == meta["current-version-id"])
    schema = next(s for s in meta["schemas"] if s["schema-id"] == v["schema-id"])
    version = {"version-id": 1, "timestamp-ms": int(time.time() * 1000), "schema-id": schema["schema-id"],
               "summary": v["summary"], "representations": [{"type": "sql", "sql": sql, "dialect": "trino"}],
               "default-namespace": v.get("default-namespace", [ns])}
    if v.get("default-catalog"):
        version["default-catalog"] = v["default-catalog"]
    body = {"name": name, "schema": schema, "view-version": version, "properties": {OWNER_PROPERTY: owner}}
    st_drop, _ = call("DELETE", f"{cat.base}/namespaces/{ns}/views/{name}")
    st_make, doc = call("POST", f"{cat.base}/namespaces/{ns}/views", body)
    if st_make != 200:
        raise RuntimeError(f"forged view refused: HTTP {st_make} {error_text(doc)}")
    return [st_drop, st_make]


def graft(cat: Catalog, src_ns: str, src_t: str, sid: int, dst_ns: str, dst_t: str, ref: str) -> int:
    """Add another table's snapshot to dst (metadata only) and point dst's `ref` at it: one commit."""
    s, d = cat.table(src_ns, src_t), cat.table(dst_ns, dst_t)
    snap = dict(next(x for x in s["snapshots"] if x["snapshot-id"] == sid))
    snap.pop("parent-snapshot-id", None)
    cur = d.get("current-snapshot-id")
    if cur not in (None, -1):
        snap["parent-snapshot-id"] = cur
    snap["schema-id"] = d["current-schema-id"]
    snap["sequence-number"] = max(snap.get("sequence-number", 1), d.get("last-sequence-number", 0) + 1)
    body = {"requirements": [{"type": "assert-ref-snapshot-id", "ref": ref, "snapshot-id": ref_id(d, ref)}],
            "updates": [{"action": "add-snapshot", "snapshot": snap},
                        {"action": "set-snapshot-ref", "ref-name": ref, "type": "branch" if ref == "main" else "tag",
                         "snapshot-id": sid}]}
    st, doc = call("POST", f"{cat.base}/namespaces/{dst_ns}/tables/{dst_t}", body)
    if st != 200:
        raise RuntimeError(f"graft refused: HTTP {st} {error_text(doc)}")
    return st


# ---- management, for the scratch warehouses ----------------------------------------------------------

def bootstrap(lk: str) -> int:
    st, _ = call("POST", f"{lk}/management/v1/bootstrap", {"accept-terms-of-use": True})
    return st


def create_warehouse(lk: str, name: str, bucket: str, endpoint: str, key_env: str, secret_env: str) -> int:
    body = {"warehouse-name": name, "project-id": PROJECT,
            "storage-profile": {"type": "s3", "bucket": bucket, "key-prefix": "wh", "endpoint": endpoint,
                                "region": "local-01", "path-style-access": True, "flavor": "s3-compat",
                                "sts-enabled": False, "remote-signing-enabled": False},
            "storage-credential": {"type": "s3", "credential-type": "access-key",
                                   "aws-access-key-id": os.environ[key_env],
                                   "aws-secret-access-key": os.environ[secret_env]}}
    st, doc = call("POST", f"{lk}/management/v1/warehouse", body, timeout=30)
    if st not in (200, 201):
        raise RuntimeError(f"create warehouse {name}: HTTP {st} {error_text(doc)}")
    return st


def warehouse_id(lk: str, name: str) -> str | None:
    _, doc = call("GET", f"{lk}/management/v1/warehouse?project-id={PROJECT}")
    return next((w["id"] for w in doc.get("warehouses", []) if w["name"] == name), None)


def wipe(lk: str, name: str) -> str:
    """Drop every view, table (purged) and namespace of a scratch warehouse, then the warehouse."""
    if not name.startswith("spike_s01"):
        raise ValueError(f"refusing to wipe {name}: not a spike_s01 scratch warehouse")
    wid = warehouse_id(lk, name)
    if wid is None:
        return "absent"
    cat = Catalog(f"{lk}/catalog", name)
    for ns in cat.namespaces():
        for v in cat.views(ns):
            call("DELETE", f"{cat.base}/namespaces/{ns}/views/{v}")
        for t in cat.tables(ns):
            call("DELETE", f"{cat.base}/namespaces/{ns}/tables/{t}?purgeRequested=true", timeout=30)
        call("DELETE", f"{cat.base}/namespaces/{ns}")
    st = 0
    for _ in range(30):
        st, doc = call("DELETE", f"{lk}/management/v1/warehouse/{wid}", timeout=30)
        if st in (200, 204):
            return "deleted"
        time.sleep(2)
    return f"left behind: HTTP {st} {error_text(doc)}"


def probe(url: str, timeout: float = 4) -> str:
    t0 = time.monotonic()
    try:
        st, _ = call("GET", url, timeout=timeout)
        return f"HTTP {st}"
    except Unreachable as e:
        return f"unreachable after {time.monotonic() - t0:.1f} s: {e}"


def main(argv: list[str]) -> int:
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "probe":
            for url in args:
                print(f"{url} -> {probe(url)}")
            return 0
        if cmd == "bootstrap":
            print(f"bootstrap HTTP {bootstrap(args[0])}")
            return 0
        if cmd == "create-warehouse":
            print(f"warehouse {args[1]} HTTP {create_warehouse(*args)}")
            return 0
        cat = Catalog(args[0], args[1])
        if cmd == "commit":
            t0 = time.monotonic()
            st, doc = cat.commit(json.loads(args[2]))
            print(f"commit HTTP {st} in {(time.monotonic() - t0) * 1000:.1f} ms {'' if st == 204 else error_text(doc)}")
            return 0 if st in (200, 204) else 1
        if cmd == "forge-view":
            print(f"forged view {args[2]}.{args[3]} owner {args[4]}: HTTP {forge_view(cat, *args[2:])}")
            return 0
        if cmd == "graft":
            ns, t, sid, dns, dt, ref = args[2:]
            print(f"grafted snapshot {sid} of {ns}.{t} into {dns}.{dt} as {ref}: HTTP "
                  f"{graft(cat, ns, t, int(sid), dns, dt, ref)}")
            return 0
        if cmd == "drift":
            with open(args[2]) as f:
                found = drift(cat, json.load(f))
            print("\n".join(found) if found else "clean: catalog equals the release")
            return 1 if found else 0
    except Unreachable as e:
        print(f"unreachable: {e}")
        return 2
    except RuntimeError as e:
        print(str(e))
        return 1
    raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
