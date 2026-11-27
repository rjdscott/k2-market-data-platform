#!/usr/bin/env python3
"""The lake half of `make backup`, and `make backup-verify` (W1-22).

    backup_lake.py copy   --release /k2/releases/<id> --dir /backup --run <UTC stamp> [--dump <name>=<file>]... [--dry-run]
    backup_lake.py verify --dir <backup dir> --backup-set <backup-set.json>

`copy` runs in the release's tools image, started by scripts/backup-lake.sh on the catalog's
network with the backup storage key (K2_BACKUP_S3_*: read-only on every warehouse prefix and on
k2-landing, write nowhere in the lake), the releases root read-only and the backup directory the
one writable mount. It copies what the release's compiled/lake/backup-set.json lists:

- each `restore` table, the files of its snapshot at its ref: the current metadata file (from the
  catalog), the snapshot's manifest list, every manifest in it, every data and delete file those
  manifests hold (an entry marked deleted is not part of the snapshot), and the snapshot's
  statistics files. The manifests are Avro, read by the small decoder below (the tools image has
  no Avro library, and adding one for two schemas is not worth a dependency). A `rebuild` table is
  listed in the manifest and not copied;
- each landing prefix of k2-landing, every object under it;
- the releases root (/k2/releases), as one tar that keeps its links and modes.

Objects land at runs/.<stamp>.partial/objects/<bucket>/<key>; a file the previous complete run
already holds at the same key and size is hard-linked, not fetched again (Iceberg and landing
objects are written once under a unique key). manifest.json is written last, and the directory is
renamed to runs/<stamp> only then: a directory without the leading dot is a complete copy.

`verify` reads the newest complete run's manifest, prints each table with its snapshot id and
counts and each landing prefix, re-checks every file's presence and size and the sha256 of each
dump and of the releases tar, and fails on any mismatch or on a `restore` table of the given
backup set (the live release's) that the run lacks. It needs nothing but the backup directory.

Nothing here writes to the lake: every storage call is a GET. The key is read from the environment
by name, sent only in a signed header (scripts/access_verify.py's sigv4), and never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

CATALOG = "http://lakekeeper:8181/catalog/v1"  # on k2-catalog only: no host port (ADR-059 part 7)
KEY = ("K2_BACKUP_S3_ACCESS_KEY", "K2_BACKUP_S3_SECRET_KEY")
DELETED = 2  # a manifest entry's status: the file left the table in this snapshot
CHUNK = 1 << 20
RELEASES = Path("/k2/releases")  # the releases root, mounted read-only


class Failed(Exception):
    pass


# ---- Avro object container files (the Iceberg manifest list and manifests) ----------------------

def _long(b: bytes, p: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        x = b[p]
        p += 1
        n |= (x & 0x7F) << shift
        shift += 7
        if not x & 0x80:
            return (n >> 1) ^ -(n & 1), p


def _names(s: Any, names: dict[str, Any]) -> None:
    """Every named type in a schema, so a later reference by name resolves."""
    if isinstance(s, list):
        for x in s:
            _names(x, names)
    elif isinstance(s, dict):
        if s.get("type") in ("record", "enum", "fixed"):
            names[s["name"]] = s
            if s.get("namespace"):
                names[f"{s['namespace']}.{s['name']}"] = s
        for f in s.get("fields", []):
            _names(f["type"], names)
        for k in ("items", "values"):
            if k in s:
                _names(s[k], names)


def _read(b: bytes, p: int, s: Any, names: dict[str, Any]) -> tuple[Any, int]:
    if isinstance(s, list):  # a union: the branch index, then the branch
        i, p = _long(b, p)
        return _read(b, p, s[i], names)
    t = s["type"] if isinstance(s, dict) else s
    if isinstance(t, (dict, list)):
        return _read(b, p, t, names)
    if t == "null":
        return None, p
    if t == "boolean":
        return b[p] == 1, p + 1
    if t in ("int", "long"):
        return _long(b, p)
    if t == "float":
        return struct.unpack_from("<f", b, p)[0], p + 4
    if t == "double":
        return struct.unpack_from("<d", b, p)[0], p + 8
    if t in ("bytes", "string"):
        n, p = _long(b, p)
        raw = b[p:p + n]
        return (raw.decode() if t == "string" else raw), p + n
    if t == "record":
        out = {}
        for f in s["fields"]:
            out[f["name"]], p = _read(b, p, f["type"], names)
        return out, p
    if t == "enum":
        i, p = _long(b, p)
        return s["symbols"][i], p
    if t == "fixed":
        return b[p:p + s["size"]], p + s["size"]
    if t in ("array", "map"):
        items: list[Any] = []
        while True:
            n, p = _long(b, p)
            if n == 0:
                return (dict(items) if t == "map" else items), p
            if n < 0:  # a block that also gives its size in bytes
                n = -n
                _, p = _long(b, p)
            for _ in range(n):
                if t == "map":
                    k, p = _read(b, p, "string", names)
                    v, p = _read(b, p, s["values"], names)
                    items.append((k, v))
                else:
                    v, p = _read(b, p, s["items"], names)
                    items.append(v)
    if t in names:
        return _read(b, p, names[t], names)
    raise Failed(f"Avro type {t!r} is not one this reader knows")


def avro_records(data: bytes) -> list[Any]:
    """Every record of an Avro object container file (codec null or deflate, Iceberg's default)."""
    if data[:4] != b"Obj\x01":
        raise Failed("not an Avro object container file")
    meta, p = _read(data, 4, {"type": "map", "values": "bytes"}, {})
    sync, p = data[p:p + 16], p + 16
    schema = json.loads(meta["avro.schema"])
    codec = meta.get("avro.codec", b"null").decode()
    if codec not in ("null", "deflate"):
        raise Failed(f"Avro codec {codec} is not one this reader knows")
    names: dict[str, Any] = {}
    _names(schema, names)
    out: list[Any] = []
    while p < len(data):
        count, p = _long(data, p)
        size, p = _long(data, p)
        block = data[p:p + size]
        p += size
        if data[p:p + 16] != sync:
            raise Failed("an Avro block does not end at the file's sync marker")
        p += 16
        block = zlib.decompress(block, -15) if codec == "deflate" else block
        q = 0
        for _ in range(count):
            r, q = _read(block, q, schema, names)
            out.append(r)
    return out


# ---- the plan: what a backup set asks for ------------------------------------------------------

def split(uri: str) -> tuple[str, str]:
    """s3://bucket/key -> (bucket, key), refused unless the key is a plain relative path."""
    if not uri.startswith("s3://"):
        raise Failed(f"{uri}: not an s3:// path")
    bucket, _, key = uri[5:].partition("/")
    parts = key.split("/")
    if not bucket or not key or any(x in ("", ".", "..") for x in parts):
        raise Failed(f"{uri}: not a plain object path")
    return bucket, key


def snapshot_files(meta: dict[str, Any], ref: str, read: Callable[[str], bytes]) -> tuple[int | None, list[tuple[str, int | None]]]:
    """(the snapshot id at `ref`, every file reading the table at it needs, with its size when the
    metadata gives one). (None, []) for a table with no snapshot at the ref yet."""
    sid = (meta.get("refs") or {}).get(ref, {}).get("snapshot-id")
    if sid is None and ref == "main" and meta.get("current-snapshot-id") not in (None, -1):
        sid = meta["current-snapshot-id"]  # format v1 metadata may carry no refs
    if sid is None:
        return None, []
    snap = next((s for s in meta.get("snapshots", []) if s["snapshot-id"] == sid), None)
    if snap is None:
        raise Failed(f"ref {ref} names snapshot {sid}, which the metadata does not hold")
    files: list[tuple[str, int | None]] = [(snap["manifest-list"], None)]
    for m in avro_records(read(snap["manifest-list"])):
        files.append((m["manifest_path"], m["manifest_length"]))
        for e in avro_records(read(m["manifest_path"])):
            if e["status"] != DELETED:
                files.append((e["data_file"]["file_path"], e["data_file"]["file_size_in_bytes"]))
    for s in [*meta.get("statistics", []), *meta.get("partition-statistics", [])]:
        if s["snapshot-id"] == sid:
            files.append((s["statistics-path"], s.get("file-size-in-bytes")))
    return sid, list(dict.fromkeys(files))


def plan(backup_set: dict[str, Any], locate: Callable[[str], tuple[str, str]], read: Callable[[str], bytes],
         ls: Callable[[str, str], list[tuple[str, int]]]) -> dict[str, Any]:
    """What one run copies. locate(table) -> (location, metadata file) from the catalog; read(uri) ->
    bytes; ls(bucket, prefix) -> [(key, size)]. The catalog is unauthenticated, so every path it
    or the metadata names must sit under the table's location: nothing else is read or written."""
    tables, landings, rebuild = [], [], []
    for e in backup_set["entries"]:
        if e["kind"] == "table" and e["mode"] == "rebuild":
            rebuild.append(e["table"])
        elif e["kind"] == "table":
            loc, meta_uri = locate(e["table"])
            loc = loc.rstrip("/")
            split(loc + "/x")
            sid, files = snapshot_files(json.loads(read(meta_uri)), e["ref"], read)
            files = [(meta_uri, None), *files]
            for uri, _ in files:
                split(uri)
                if not uri.startswith(loc + "/"):
                    raise Failed(f"{e['table']}: {uri} is outside the table's location {loc}")
            tables.append({"table": e["table"], "ref": e["ref"], "location": loc, "metadata_location": meta_uri,
                           "snapshot_id": sid, "files": files})
        elif e["kind"] == "landing":
            found = ls(e["bucket"], e["prefix"])
            for k, _ in found:
                split(f"s3://{e['bucket']}/{k}")
            landings.append({"bucket": e["bucket"], "prefix": e["prefix"],
                             "files": [(f"s3://{e['bucket']}/{k}", n) for k, n in found]})
    return {"tables": tables, "landings": landings, "rebuild": sorted(rebuild),
            "kinds": sorted({e["kind"] for e in backup_set["entries"]})}


# ---- storage, the catalog -------------------------------------------------------------------

def _av() -> Any:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import access_verify  # noqa: E402  (scripts/: its sigv4 and s3 are the repo's one SigV4 client)
    return access_verify


def key_pair() -> tuple[str, str]:
    missing = [n for n in KEY if not os.environ.get(n)]
    if missing:
        raise Failed(f"no backup storage key: {' and '.join(missing)} not set")
    return os.environ[KEY[0]], os.environ[KEY[1]]


def endpoint() -> str:
    return os.environ.get("K2_S3_ENDPOINT") or _av().S3_ENDPOINT


def get(uri: str, out: Path | None = None) -> bytes:
    """GET one object: its bytes, or streamed to `out` (returning b"")."""
    av, (bucket, key) = _av(), split(uri)
    path = urllib.parse.quote(f"/{bucket}/{key}", safe="/-_.~")
    url = urllib.parse.urlsplit(endpoint())
    headers = av.sigv4("GET", url.netloc, path, "", time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), key_pair())
    try:
        with urllib.request.urlopen(urllib.request.Request(endpoint().rstrip("/") + path, headers=headers), timeout=60) as r:
            if out is None:
                return bytes(r.read())
            with open(out, "wb") as f:
                shutil.copyfileobj(r, f, CHUNK)
            return b""
    except urllib.error.HTTPError as e:
        raise Failed(f"{uri}: storage answered HTTP {e.code}") from None
    except OSError as e:
        raise Failed(f"{uri}: cannot reach the storage ({type(e).__name__})") from None


def ls(bucket: str, prefix: str) -> list[tuple[str, int]]:
    av, out, token = _av(), [], ""
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    while True:
        q = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
        status, code, body = av.s3(endpoint(), key_pair(), "GET", bucket, query=q)
        if status != 200:
            raise Failed(f"listing s3://{bucket}/{prefix}: {av.answered(status, code, body)}")
        doc = ET.fromstring(body)
        out += [(c.findtext(f"{ns}Key") or "", int(c.findtext(f"{ns}Size") or 0)) for c in doc.iter(f"{ns}Contents")]
        token = doc.findtext(f"{ns}NextContinuationToken") or ""
        if doc.findtext(f"{ns}IsTruncated") != "true" or not token:
            return out


def locate(table: str) -> tuple[str, str]:
    """(location, metadata file) of <warehouse>.<namespace>.<table>, from the catalog. The answer is
    never printed: its config block can carry storage settings."""
    wh, ns, name = table.split(".")

    def answer(path: str) -> Any:
        try:
            with urllib.request.urlopen(f"{CATALOG}/{path}", timeout=30) as r:
                return json.load(r)
        except (OSError, ValueError) as e:
            raise Failed(f"{table}: the catalog did not answer GET /{path.split('?')[0]} ({type(e).__name__})") from None

    prefix = answer(f"config?warehouse={urllib.parse.quote(wh)}")["defaults"]["prefix"]
    d = answer(f"{prefix}/namespaces/{urllib.parse.quote(ns)}/tables/{urllib.parse.quote(name)}")
    return d["metadata"]["location"], d["metadata-location"]


# ---- the copy ---------------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def complete_runs(runs: Path) -> list[Path]:
    """The complete runs, oldest first: their stamps sort by time; a dot-name is never complete."""
    return sorted(p for p in runs.iterdir() if p.is_dir() and not p.name.startswith(".")) if runs.is_dir() else []


def put(uri: str, size: int | None, tmp: Path, prev: Path | None, fetch: Callable[[str, Path], None]) -> tuple[str, int, bool]:
    """One object into tmp/objects/<bucket>/<key>: (that relative path, its size, fetched or linked)."""
    rel = "objects/{}/{}".format(*split(uri))
    dst = tmp / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    old = prev / rel if prev else None
    if old and old.is_file() and size is not None and old.stat().st_size == size:
        os.link(old, dst)
        return rel, size, False
    fetch(uri, dst)
    got = dst.stat().st_size
    if size is not None and got != size:
        raise Failed(f"{uri}: {got} bytes copied, {size} expected")
    return rel, got, True


def copy(p: dict[str, Any], tmp: Path, prev: Path | None, fetch: Callable[[str, Path], None],
         releases: Path | None) -> dict[str, Any]:
    """Every object of the plan into tmp, then the releases tar; the manifest's body."""
    tally = {"objects_copied": 0, "bytes_copied": 0}

    def files(pairs: Iterable[tuple[str, int | None]]) -> dict[str, int]:
        out = {}
        for uri, size in pairs:
            rel, n, fetched = put(uri, size, tmp, prev, fetch)
            out[rel] = n
            tally["objects_copied"] += fetched
            tally["bytes_copied"] += n if fetched else 0
        return out

    tables = []
    for t in p["tables"]:
        got = files(t["files"])
        tables.append({k: t[k] for k in ("table", "ref", "location", "metadata_location", "snapshot_id")}
                      | {"objects": len(got), "bytes": sum(got.values()), "files": got})
    landings = []
    for g in p["landings"]:
        got = files(g["files"])
        landings.append({"bucket": g["bucket"], "prefix": g["prefix"], "objects": len(got), "bytes": sum(got.values()), "files": got})
    out: dict[str, Any] = {"tables": tables, "rebuild": p["rebuild"], "landings": landings, **tally}
    if releases is not None:
        with tarfile.open(tmp / "releases.tar", "w") as tar:  # links stay links; modes, owners and times kept
            tar.add(releases, arcname="k2-releases")
        out["releases"] = {"file": "releases.tar", "bytes": (tmp / "releases.tar").stat().st_size, "sha256": sha256(tmp / "releases.tar")}
    return out


def run_copy(a: argparse.Namespace) -> int:
    release, d = Path(a.release), Path(a.dir)
    backup_set = json.loads((release / "compiled/lake/backup-set.json").read_text())
    p = plan(backup_set, locate, get, ls)
    for t in p["tables"]:
        print(f"backup-lake: {t['table']} at {t['ref']}: snapshot {t['snapshot_id']}, {len(t['files'])} files")
    for g in p["landings"]:
        print(f"backup-lake: landing s3://{g['bucket']}/{g['prefix']}: {len(g['files'])} objects")
    for name in p["rebuild"]:
        print(f"backup-lake: {name}: rebuild, not copied")
    if a.dry_run:
        print("backup-lake: DRY RUN: nothing copied")
        return 0
    runs = d / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    for stale in runs.glob(".*.partial"):  # a run that failed or was killed: never complete
        shutil.rmtree(stale)
    prev = (complete_runs(runs) or [None])[-1]
    tmp = runs / f".{a.run}.partial"
    tmp.mkdir()
    body = copy(p, tmp, prev, lambda uri, dst: get(uri, dst), RELEASES if "releases" in p["kinds"] else None)
    dumps = {}
    for spec in a.dump:
        name, _, rel = spec.partition("=")
        f = d / rel
        dumps[name] = {"file": rel, "bytes": f.stat().st_size, "sha256": sha256(f)}
    manifest = {"run": a.run, "release": release.name, "tag": backup_set["tag"],
                "backup_set_sha256": sha256(release / "compiled/lake/backup-set.json"), **body, "dumps": dumps}
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    os.rename(tmp, runs / a.run)
    print(f"backup-lake: run runs/{a.run}: {body['objects_copied']} objects, {body['bytes_copied']} bytes fetched")
    return 0


# ---- verify -----------------------------------------------------------------------------------

def check(d: Path, backup_set: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(lines to print, problems) for the newest complete run under d/runs."""
    runs = complete_runs(d / "runs")
    if not runs:
        return [], [f"no complete backup run under {d / 'runs'}"]
    run = runs[-1]
    try:
        m = json.loads((run / "manifest.json").read_text())
    except (OSError, ValueError):
        return [], [f"{run}: no readable manifest.json"]
    lines = [f"run {run.name}: release {m['release']} (tag {m['tag']})"]
    bad = []

    def files(what: str, got: dict[str, int]) -> None:
        for rel, size in got.items():
            f = run / rel
            if not f.is_file():
                bad.append(f"{what}: {rel} is missing")
            elif f.stat().st_size != size:
                bad.append(f"{what}: {rel} is {f.stat().st_size} bytes, the manifest says {size}")

    for t in m["tables"]:
        lines.append(f"table {t['table']} at {t['ref']}: snapshot {t['snapshot_id']}, {t['objects']} files, {t['bytes']} bytes")
        files(t["table"], t["files"])
    for g in m["landings"]:
        lines.append(f"landing s3://{g['bucket']}/{g['prefix']}: {g['objects']} objects, {g['bytes']} bytes")
        files(f"landing {g['prefix']}", g["files"])
    for name, x in [*m["dumps"].items(), *([("releases", m["releases"])] if "releases" in m else [])]:
        f = run / x["file"] if name == "releases" else d / x["file"]
        if not f.is_file() or sha256(f) != x["sha256"]:
            bad.append(f"{name}: {x['file']} is missing or its sha256 differs from the manifest's")
        else:
            lines.append(f"{name}: {x['file']}, {x['bytes']} bytes, sha256 matches")
    held = {t["table"] for t in m["tables"]}
    for e in backup_set["entries"]:
        if e["kind"] == "table" and e["mode"] == "restore" and e["table"] not in held:
            bad.append(f"{e['table']}: a restore table of the live backup set that this run does not hold")
    for kind, ok in (("releases", "releases" in m), ("lakekeeper-db", "lakekeeper" in m["dumps"])):
        if any(e["kind"] == kind for e in backup_set["entries"]) and not ok:
            bad.append(f"{kind}: in the live backup set, not in this run")
    return lines, bad


def run_verify(a: argparse.Namespace) -> int:
    try:
        backup_set = json.loads(Path(a.backup_set).read_text())
    except (OSError, ValueError):
        print(f"backup-verify: FAIL: no live backup set at {a.backup_set}", file=sys.stderr)
        return 1
    lines, bad = check(Path(a.dir), backup_set)
    for line in lines:
        print(f"backup-verify: {line}")
    for b in bad:
        print(f"backup-verify: FAIL: {b}", file=sys.stderr)
    print(f"backup-verify: {'FAIL' if bad else 'OK'}: {len(bad)} problem(s)")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="backup_lake.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("copy")
    c.add_argument("--release", required=True)
    c.add_argument("--dir", required=True)
    c.add_argument("--run", required=True)
    c.add_argument("--dump", action="append", default=[])
    c.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("--dir", required=True)
    v.add_argument("--backup-set", required=True)
    a = ap.parse_args(argv)
    try:
        return run_copy(a) if a.cmd == "copy" else run_verify(a)
    except Failed as e:
        print(f"backup-lake: FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
