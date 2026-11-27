"""The document-set adapter (A2), filled by step S04.

In 3.0 the store is git: a set is every file of model.DOCUMENT_ROOTS at an annotated release
tag, with its provenance record. A root the tag does not hold (`domains/` before the first
parser, or at an older tag) is left out, never an error: `git ls-tree <tag>` lists what is
there. Each document is the tag's blob, read with `git cat-file`, not extracted by
`git archive`: archive passes blobs through the checkout's eol and filter settings and the
tree's `export-subst`, so one tag could give two sets on two machines, or on a re-run once a
new ref names the commit. A blob is the bytes the tag approved, on every machine, every time.
After 3.0 only this module changes, to read a registry's approved version (ADR-055 part 7).
"""

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from k2gen.model import (
    DOCUMENT_ROOTS,
    PROVENANCE,
    DocumentError,
    DocumentProvenance,
    DocumentSet,
    Provenance,
    schema_errors,
    set_sha256,
)

_PR = re.compile(r"\(#([0-9]+)\)")  # a squash merge's subject ends `(#NNN)`


def _git(repo: Path, *args: str) -> bytes:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if done.returncode:
        raise ValueError(f"git {' '.join(args)}: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout


def load(tag: str, repo: Path) -> DocumentSet:
    """The document set of an annotated release tag in `repo`, with its provenance."""
    ref = f"refs/tags/{tag}"  # never an option, never a branch of the same name
    if _git(repo, "cat-file", "-t", ref).strip() != b"tag":
        raise ValueError(f"{tag}: not an annotated tag; its tagger is the release's approver")
    commit = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    fmt = "--format=%(taggername) %(taggeremail)%00%(taggerdate:unix)"
    tagger, _, when = _git(repo, "for-each-ref", fmt, ref).decode().strip().partition("\0")
    # ls-tree matches each root literally (`contracts/` is not `contracts-old/`) and skips a missing one
    listing = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, "--", *DOCUMENT_ROOTS).decode()
    entries = {path: meta.split() for meta, _, path in (e.partition("\t") for e in listing.split("\0") if e)}
    documents: dict[str, bytes] = {}
    listed: list[DocumentProvenance] = []
    # ponytail: two git processes per document; one `cat-file --batch` and one log walk if a set outgrows seconds
    for path in sorted(entries):
        mode, kind, oid = entries[path]
        if kind != "blob" or mode == "120000":
            raise ValueError(f"{tag}: {path} is a {'symlink' if mode == '120000' else kind}; a document is a regular file")
        data = _git(repo, "cat-file", "blob", oid)
        log = _git(repo, "log", "-1", "--no-show-signature", "--format=%H%x00%s", commit, "--", path)
        last, _, subject = log.decode().strip().partition("\0")
        prs = _PR.findall(subject)
        documents[path] = data
        listed.append(DocumentProvenance(path, hashlib.sha256(data).hexdigest(), last, int(prs[-1]) if prs else None))
    tagged_at = datetime.fromtimestamp(int(when), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    docs = DocumentSet(documents, Provenance("git", tag, commit, tagger, tagged_at, set_sha256(documents), tuple(listed)))
    _check(docs)
    return docs


def _record(p: Provenance) -> dict[str, Any]:
    """provenance.json's content (provenance.schema.json)."""
    return {"source": p.source, "tag": p.tag, "commit": p.commit, "tagger": p.tagger, "taggedAt": p.tagged_at,
            "setSha256": p.set_sha256,
            "documents": [{"path": d.path, "sha256": d.sha256, "lastCommit": d.last_commit, "pr": d.pr} for d in p.documents]}


def _check(docs: DocumentSet) -> dict[str, Any]:
    """The provenance record, refused unless it is schema-valid and lists exactly the set's
    documents with their hashes and the set's hash."""
    record = _record(docs.provenance)
    problems = schema_errors("provenance", record)
    listed = {d.path: d.sha256 for d in docs.provenance.documents}
    actual = {path: hashlib.sha256(data).hexdigest() for path, data in docs.documents.items()}
    for path in sorted(listed.keys() | actual.keys()):
        if path not in actual:
            problems.append(f"{path}: listed, but not in the set")
        elif path not in listed:
            problems.append(f"{path}: in the set, but not listed")
        elif listed[path] != actual[path]:
            problems.append(f"{path}: its bytes are not the ones listed")
    if docs.provenance.set_sha256 != set_sha256(docs.documents):
        problems.append("setSha256 is not the set's")
    if PROVENANCE in docs.documents:
        problems.append(f"{PROVENANCE} is the record, never a document")
    if problems:
        raise DocumentError("\n".join(f"{PROVENANCE}: {p}" for p in problems))
    return record


def write(docs: DocumentSet, out: Path) -> None:
    """Write the set to `out`: each document at its path, and provenance.json (model.PROVENANCE)."""
    record = _check(docs)  # its schema also refuses a path with `..` or a leading `/`
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out}: not empty; a set is written to an empty directory")
    for path, data in docs.documents.items():
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(data)
    (out / PROVENANCE).write_text(json.dumps(record, indent=2) + "\n")


def read(set_dir: Path) -> DocumentSet:
    """A set `write` wrote, or a release's documents/ directory; `--set` and `--previous` read this."""
    files: dict[str, bytes] = {}
    for p in sorted(set_dir.rglob("*")):
        if p.is_symlink():
            raise DocumentError(f"{p}: a symlink; a document set holds regular files only")
        if p.is_file():
            files[p.relative_to(set_dir).as_posix()] = p.read_bytes()
    if PROVENANCE not in files:
        raise DocumentError(f"{set_dir}: no {PROVENANCE}, so not a document set")
    try:
        record = json.loads(files.pop(PROVENANCE))
    except ValueError as e:
        raise DocumentError(f"{set_dir / PROVENANCE}: {e}") from None
    problems = schema_errors("provenance", record)
    if problems:
        raise DocumentError("\n".join(f"{set_dir / PROVENANCE}: {p}" for p in problems))
    listed = tuple(DocumentProvenance(d["path"], d["sha256"], d["lastCommit"], d["pr"]) for d in record["documents"])
    docs = DocumentSet(files, Provenance(record["source"], record["tag"], record["commit"], record["tagger"],
                                         record["taggedAt"], record["setSha256"], listed))
    _check(docs)  # the files on disk are the ones provenance.json lists, byte for byte
    return docs
