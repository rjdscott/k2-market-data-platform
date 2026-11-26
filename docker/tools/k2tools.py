#!/usr/bin/env python3
"""What scripts/deploy.py (ADR-058) must run on the far side of Docker Desktop's file sharing or on
the catalog's network, as the `tools` one-shot. Stdlib only.

    k2tools swap <release id> <pointer>...          move each pointer to ../<release id>
    k2tools pointers                                {pointer: target} as this container sees them
    k2tools namespaces <catalog uri> <storage plan> create the plan's missing namespaces
    k2tools table <catalog uri> <warehouse> <namespace> <table>
                                                    the table's columns and partition fields, or null
    k2tools sql <server> <user> <password file> <ca file> <statement>
                                                    one statement over HTTPS: {"columns", "rows"} or {"error"}

`swap` follows the release spine's experiment (c) (docs/research/2026-11-26-release-spine-spikes.md):
a pointer renamed on the host is never seen inside a container on this host, so the swap runs here,
inside the VM, as a fresh temporary link (`.<pointer>.<uuid4>`, relative target `../<id>`) renamed
over the pointer, which rename(2) does in one step. The releases root is mounted here read-write at
/k2/releases and read-only everywhere else.

Exit codes: 0 done; 1 refused (a missing warehouse, a release directory that is not there); 2 the
catalog or Trino could not be reached.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

RELEASES = Path(os.environ.get("K2_RELEASES_MOUNT", "/k2/releases"))
PROJECT = "00000000-0000-0000-0000-000000000000"  # Lakekeeper's default project


class Unreachable(Exception):
    """No HTTP answer: refused, no route, timed out, or the name does not resolve."""


def call(method: str, url: str, body: object = None, timeout: float = 10) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise Unreachable(str(getattr(e, "reason", e))) from None


# --- pointers --------------------------------------------------------------------------


def swap(release: str, pointers: list[str], root: Path = RELEASES) -> dict[str, str]:
    """Every pointer to ../<release>; idempotent. A release that is not there is refused before any
    pointer moves, so no pointer ever dangles."""
    if not (root / release / "manifest.json").is_file():
        raise SystemExit(f"k2tools: {root / release} is not a release here; no pointer moved")
    (root / "pointers").mkdir(exist_ok=True)
    for p in pointers:
        tmp = root / "pointers" / f".{p}.{uuid.uuid4().hex}"
        os.symlink(f"../{release}", tmp)
        os.replace(tmp, root / "pointers" / p)  # mv -T: the pointer is never absent
    return read_pointers(root)


def read_pointers(root: Path = RELEASES) -> dict[str, str]:
    d = root / "pointers"
    if not d.is_dir():
        return {}
    return {p.name: os.readlink(p) for p in sorted(d.iterdir()) if p.is_symlink() and not p.name.startswith(".")}


# --- the catalog -----------------------------------------------------------------------


def prefix(uri: str, warehouse: str) -> str | None:
    """The warehouse's REST prefix, or None when Lakekeeper has no such warehouse."""
    st, cfg = call("GET", f"{uri}/v1/config?warehouse={warehouse}")
    if st != 200:
        return None
    return f"{uri}/v1/{cfg['defaults']['prefix']}"


def namespaces(uri: str, plan: dict) -> list[str]:
    """Create every namespace of the storage plan that is missing (never drop, never alter): the
    lines it printed. A warehouse the catalog lacks is refused: lake-init makes warehouses."""
    lines = []
    for w in plan["warehouses"]:
        base = prefix(uri, w["name"])
        if base is None:
            raise SystemExit(f"k2tools: warehouse {w['name']} is not in the catalog; lake-init creates warehouses")
        st, doc = call("GET", f"{base}/namespaces")
        have = {ns[0] for ns in doc.get("namespaces", [])} if st == 200 else set()
        for ns in w["namespaces"]:
            if ns in have:
                continue
            st, doc = call("POST", f"{base}/namespaces", {"namespace": [ns]})
            if not 200 <= st < 300 and st != 409:  # 409: made since the listing
                raise SystemExit(f"k2tools: namespace {w['name']}.{ns}: HTTP {st}")
            lines.append(f"created namespace {w['name']}.{ns}")
    return lines


def _type(t: object) -> str:
    """An Iceberg field type as one string: list<x>, map<k,v>, or the primitive's own spelling."""
    if isinstance(t, dict):
        if t["type"] == "list":
            return f"list<{_type(t['element'])}>"
        if t["type"] == "map":
            return f"map<{_type(t['key'])},{_type(t['value'])}>"
        raise SystemExit(f"k2tools: a {t['type']} column is not one a K2 table holds")
    return str(t).replace(" ", "")


def table(uri: str, warehouse: str, namespace: str, name: str) -> dict | None:
    """The current schema's columns [name, type, required] in order and the default partition
    spec's fields [transform, source column], or None when there is no such table."""
    base = prefix(uri, warehouse)
    if base is None:
        return None
    st, doc = call("GET", f"{base}/namespaces/{namespace}/tables/{name}")
    if st == 404:
        return None
    if st != 200:
        raise SystemExit(f"k2tools: {warehouse}.{namespace}.{name}: HTTP {st}")
    meta = doc["metadata"]
    schema = next(s for s in meta["schemas"] if s["schema-id"] == meta["current-schema-id"])
    by_id = {f["id"]: f["name"] for f in schema["fields"]}
    spec = next(s for s in meta["partition-specs"] if s["spec-id"] == meta["default-spec-id"])
    return {"columns": [[f["name"], _type(f["type"]), f["required"]] for f in schema["fields"]],
            "partitioning": [[f["transform"], by_id.get(f["source-id"], "?")] for f in spec["fields"]]}


# --- Trino -----------------------------------------------------------------------------


class TrinoError(Exception):
    def __init__(self, name: str, message: str):
        super().__init__(f"{name}: {message}")
        self.name = name


def sql(server: str, user: str, password: str, ca: str, statement: str, timeout: float = 120) -> dict:
    """One statement, password login over HTTPS: {"columns": [[name, type]], "rows": [...]}. A
    refusal or a query error is TrinoError (AUTHENTICATION_FAILED for a 401); no answer is
    Unreachable. The password goes only into the Authorization header."""
    context = ssl.create_default_context(cafile=ca)
    auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    headers = {"X-Trino-User": user, "Authorization": auth, "X-Trino-Source": "k2-deploy"}

    def fetch(url: str, data: bytes | None = None) -> dict:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=30, context=context) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise TrinoError("AUTHENTICATION_FAILED", f"{user} was not logged in") from None
            raise TrinoError(f"HTTP_{e.code}", e.reason) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise Unreachable(str(getattr(e, "reason", e))) from None

    doc = fetch(f"{server}/v1/statement", statement.encode())
    columns: list[list[str]] = []
    rows: list[list[object]] = []
    deadline = time.monotonic() + timeout
    while True:
        columns = columns or [[c["name"], c["type"]] for c in doc.get("columns") or []]
        rows += doc.get("data") or []
        if "error" in doc:
            e = doc["error"]
            raise TrinoError(e.get("errorName", "ERROR"), e.get("message", ""))
        if not doc.get("nextUri"):
            return {"columns": columns, "rows": rows}
        if time.monotonic() > deadline:
            raise TimeoutError(statement[:80])
        doc = fetch(doc["nextUri"])


def main(argv: list[str]) -> int:
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "swap" and len(args) >= 2:
            print(json.dumps(swap(args[0], args[1:]), sort_keys=True))
        elif cmd == "pointers" and not args:
            print(json.dumps(read_pointers(), sort_keys=True))
        elif cmd == "namespaces" and len(args) == 2:
            for line in namespaces(args[0], json.loads(Path(args[1]).read_text())):
                print(line)
        elif cmd == "table" and len(args) == 4:
            print(json.dumps(table(*args)))
        elif cmd == "sql" and len(args) == 5:
            server, user, password_file, ca, statement = args
            try:
                print(json.dumps(sql(server, user, Path(password_file).read_text().strip(), ca, statement)))
            except TrinoError as e:
                print(json.dumps({"error": e.name, "message": str(e)}))
                return 1
        else:
            print(__doc__, file=sys.stderr)
            return 64
    except Unreachable as e:
        print(f"k2tools: unreachable: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
