"""The compiler, `k2gen release` (A1, A3), filled by step S08.

Pure: its only inputs are a document set, the previous release, the image refs and the set's
compose file. Every file is made in memory first: documents/ (the set, as docset.write writes
it: each document at its path and provenance.json), provenance.json, model.RELEASE_COMPOSE (the
compose file, byte for byte) and every emitter's files through k2gen.emit.run (a stub is skipped
with a line on stderr until S23; two emitters writing one path is an error). Every emitted file
with a schema (model.SCHEMAS) is validated against it, then manifest.json, whose `files` hash
every other file and whose `needs` follow model.need_of against `previous`. Only then is
<out_root>/.<id>.tmp/ written, made read-only and renamed to <out_root>/<id>/ in one step.

Reproducible: no clock (`builtAt` is the set's `taggedAt`), no absolute path, sorted keys and
bytes written as they were made, so two machines compile one set to one tree. A sealed release is
never rewritten: compiling its set again finds it, verifies it and reports it.
"""

import hashlib
import json
import os
import shutil
import sys
from collections.abc import Mapping
from fnmatch import fnmatchcase
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from k2gen import docset, emit, model
from k2gen.model import Manifest, ManifestProduct, Needs, Release

# Services deploy never recreates (A1, manifest.schema.json needs.recreate): captures, Redpanda,
# MinIO, Postgres, Lakekeeper. A change to one is left to an operator (diff names it).
NEVER_RECREATED = ("redpanda", "minio", "prefect-db", "lakekeeper")
# The emitted files that have a schema, by path pattern.
EMITTED_SCHEMAS = {
    model.STORAGE_PLAN: "storage-plan",
    model.BACKUP_SET: "backup-set",
    model.DAGS: "dags",
    model.ONESHOTS: "oneshots",
    model.CATALOG_INDEX: "catalog-index",
    model.pipeline_path("*"): "pipeline",
    model.catalog_path("*"): "catalog-product",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes, kind: str, where: object) -> Any:
    doc = json.loads(data)
    if errors := model.schema_errors(kind, doc):
        raise ValueError(f"{where}: " + "; ".join(errors))
    return doc


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under `root` by release-relative path. A symlink or any other non-plain entry is
    an error: nothing in a release may point outside it."""
    files = {}
    for here, dirs, names in os.walk(root):
        for name in dirs + names:
            path = Path(here, name)
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError(f"{path}: a symlink or special file; a release holds plain files only")
        for name in names:
            files[Path(here, name).relative_to(root).as_posix()] = Path(here, name).read_bytes()
    return files


def recreatable(service: str) -> bool:
    return service not in NEVER_RECREATED and not service.startswith("capture-")


def changed_services(old_compose: bytes | None, new_compose: bytes, old_images: Mapping[str, str], new_images: Mapping[str, str]) -> list[str]:
    """The new compose file's services whose definition or image differs from the old; every one
    when there is no old file. Anchors and merge keys are resolved, so each service is compared
    as compose itself reads it."""
    old = (yaml.safe_load(old_compose) or {}).get("services") or {} if old_compose else {}
    new = (yaml.safe_load(new_compose) or {}).get("services") or {}
    return sorted(s for s in new if old.get(s) != new[s] or old_images.get(s) != new_images.get(s))


def _dag_tasks(data: bytes | None) -> dict[str, list[str]]:
    return {d["dagId"]: d["tasks"] for d in json.loads(data)["dags"]} if data else {}


def needs(previous: Release | None, files: Mapping[str, bytes], images: Mapping[str, str]) -> Needs:
    """What deploying a release of `files` (every file but the manifest) and `images` over
    `previous` requires; the first release (None) needs everything. Every path is classified by
    model.need_of, so a new kind of file is a KeyError here before it can reach a deploy."""
    old_files = previous.manifest.files if previous else {}
    kinds = {p: model.need_of(p) for p in files.keys() | old_files.keys()}
    moved = {p for p in kinds if old_files.get(p) != (_sha(files[p]) if p in files else None)}

    def old(path: str) -> bytes | None:
        return (previous.root / path).read_bytes() if previous and path in old_files else None

    old_images = previous.manifest.images if previous else {}
    services = changed_services(old(model.RELEASE_COMPOSE), files[model.RELEASE_COMPOSE], old_images, images)
    old_dags, new_dags = _dag_tasks(old(model.DAGS)), _dag_tasks(files.get(model.DAGS))
    return Needs(
        recreate=tuple(s for s in services if recreatable(s)),
        trino_restart=previous is None or any(kinds[p] == "trinoRestart" for p in moved),
        # a DAG that gained, lost or reordered a task, or went: a new DAG has no run to drain
        dag_shape_change=previous is None or any(new_dags.get(d) != t for d, t in old_dags.items()),
        ddl=tuple(sorted({PurePosixPath(p).name.removesuffix(".sql") for p in moved & files.keys() if kinds[p] == "ddl"})),
    )


def release(set_dir: Path, previous: Path | None, images: Path, out_root: Path, compose: Path) -> str:
    """Compile the set at `set_dir` against the release directory `previous` (None for the
    first), with `images` a JSON file of service to image ref (model.SCHEMAS["images"]) and
    `compose` the compose file the release runs (the CLI's default: the set's own
    model.COMPOSE); return the new release id (model.release_id). Compiling one set twice
    gives one id."""
    refs = dict(_json(images.read_bytes(), "images", f"{images} (each image by digest, or a local build <name>:<image id 12>)"))
    docs = docset.read(set_dir)
    prov = docs.provenance
    if model.set_sha256(docs.documents) != prov.set_sha256:
        raise ValueError(f"{set_dir}: its documents do not hash to its provenance's setSha256")
    provenance = (set_dir / model.PROVENANCE).read_bytes()  # copied unchanged into the release
    loaded = model.load(docs, refs)

    files = {f"{model.DOCUMENTS_DIR}/{p}": data for p, data in docs.documents.items()}
    files |= {f"{model.DOCUMENTS_DIR}/{model.PROVENANCE}": provenance, model.PROVENANCE: provenance,
              model.RELEASE_COMPOSE: compose.read_bytes()}
    for path, text in emit.run(loaded).items():
        files[path] = text.encode()
        if kind := next((k for pattern, k in EMITTED_SCHEMAS.items() if fnmatchcase(path, pattern)), None):
            _json(files[path], kind, path)
    for path in files:
        if path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/")):
            raise ValueError(f"{path}: not a plain relative path; it would escape the release root")
    if clash := sorted({str(d) for p in files for d in PurePosixPath(p).parents} & files.keys()):
        raise ValueError(f"{clash[0]}: both a file and a directory of the release")

    prev = read(previous) if previous else None
    hashes = {p: _sha(data) for p, data in files.items()}
    rid = model.release_id(prov.tag, hashes, refs)
    need = needs(prev, files, refs)
    manifest = {
        "schema": 1, "release": rid, "tag": prov.tag, "commit": prov.commit,
        "builtAt": prov.tagged_at,  # the set's approval time, not the clock, so the tree is reproducible
        "k2gen": version("k2gen"), "previous": prev.id if prev else None, "setSha256": prov.set_sha256,
        "products": [{"id": p.id, "version": p.version, "status": p.status,
                      "sha256": model.set_sha256({k: v for k, v in docs.documents.items() if k.startswith(f"{p.path}/")})}
                     for p in sorted(loaded.products, key=lambda p: p.id)],
        "files": hashes, "images": refs,
        "needs": {"recreate": list(need.recreate), "trinoRestart": need.trino_restart,
                  "dagShapeChange": need.dag_shape_change, "ddl": list(need.ddl)},
    }
    if errors := model.schema_errors("manifest", manifest):
        raise ValueError(f"{rid}: manifest.json fails its schema: " + "; ".join(errors))
    files[model.MANIFEST] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    out_root.mkdir(parents=True, exist_ok=True)
    # ponytail: one compile per releases root at a time; a second, concurrent one would remove
    # this one's temp. Add an flock on the root if CI and deploy ever compile into the same one.
    for stale in out_root.glob(".*.tmp"):  # what a crashed compile left
        if stale.is_dir() and not stale.is_symlink():
            _remove(stale)
    final = out_root / rid
    if final.exists() or final.is_symlink():
        existing = read(final)  # verifies every file against its manifest
        if existing.manifest.files != hashes or existing.manifest.images != refs:
            raise ValueError(f"{final}: a release with this id holds different content; it is never rewritten")
        against = "" if existing.manifest.previous == manifest["previous"] else f" (its needs are against {existing.manifest.previous})"
        print(f"k2gen release: {rid} exists, identical; nothing written{against}", file=sys.stderr)
        return rid
    tmp = out_root / f".{rid}.tmp"
    _write(tmp, files)
    _seal(tmp)
    os.rename(tmp, final)
    return rid


def _write(root: Path, files: Mapping[str, bytes]) -> None:
    root.mkdir()
    for path, data in sorted(files.items()):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as f:
            f.write(data)


def _seal(root: Path) -> None:
    """chmod -R a-w, with modes set outright so the umask leaves no trace."""
    for here, _, names in os.walk(root, topdown=False):
        for name in names:
            os.chmod(Path(here, name), 0o444)
        os.chmod(here, 0o555)


def _remove(path: Path) -> None:
    for here, _, _ in os.walk(path):
        os.chmod(here, 0o700)
    shutil.rmtree(path)


def read(release_dir: Path) -> Release:
    """Open a sealed release directory, validating its manifest: its schema, its id against its
    files, images and directory name, and every file on disk against its hash."""
    root = release_dir.resolve()  # a pointer resolves once
    files = _tree(root)
    if model.MANIFEST not in files:
        raise ValueError(f"{root}: no {model.MANIFEST}, so not a release")
    doc = _json(files.pop(model.MANIFEST), "manifest", root / model.MANIFEST)
    if doc["release"] != root.name or doc["release"] != model.release_id(doc["tag"], doc["files"], doc["images"]):
        raise ValueError(f"{root}: its manifest names release {doc['release']}, which is not its directory's name or its files' id")
    if bad := sorted(p for p in files.keys() | doc["files"].keys() if doc["files"].get(p) != (_sha(files[p]) if p in files else None)):
        raise ValueError(f"{root}: files differ from its manifest: {', '.join(bad)}")
    n = doc["needs"]
    return Release(doc["release"], root, Manifest(
        release=doc["release"], tag=doc["tag"], commit=doc["commit"], built_at=doc["builtAt"], k2gen=doc["k2gen"],
        previous=doc["previous"], set_sha256=doc["setSha256"],
        products=tuple(ManifestProduct(**p) for p in doc["products"]), files=doc["files"], images=doc["images"],
        needs=Needs(tuple(n["recreate"]), n["trinoRestart"], n["dagShapeChange"], tuple(n["ddl"]))))
