#!/usr/bin/env python3
"""Creates or rotates one principal's Trino password or k2-api key, and writes only its hash.

    make password PRINCIPAL=<id> [ROTATE=1] [TO_FILE=1]
    make api-key PRINCIPAL=<id> [ROTATE=1]
    make principals-status

The procedure around it: docs/runbooks/create-or-rotate-a-principal.md. Every file is under
${K2_SECRETS:-$HOME/k2-secrets}, laid out as k2gen.model.SECRET_FILES says, outside any git
checkout (refused inside one), and nothing here is ever committed, compiled or hashed into a
release:

- `password`: a 32-byte random secret (secrets.token_urlsafe, 43 characters). Its bcrypt, cost
  10 and spelt `$2y$` as Trino's file authenticator requires, replaces or appends the one line
  `<id>:<hash>` of trino/password.db; every other line, the release canary's included, is kept
  byte for byte and in place. The secret is printed once on stdout, which must be a terminal.
  With TO_FILE=1, for a service principal only (dbt_*, pub_*, svc.*), it is instead written to
  principals/<id>.password, mode 600, for the containers that mount it
  (model.password_mount), and printed nowhere.
- `api-key`: `k2_` and a 32-byte random secret, printed once on stdout, which must be a
  terminal. api/keys.json maps the sha256 hex of the whole key, `k2_` included, to the
  principal; a rotation drops the principal's old key. Only a principal k2-api may act as
  (compiled/api/principals.json, k2gen.emit.api) gets one.
- `status`: each known principal's id and which of the three it has; never a hash or a secret.

A principal is known when contracts/access/principals.yaml declares it or it is the builder or
publisher of a dbt or code product (model.builder, model.publisher): anything else is refused,
the release canary included (deploy writes its line, S19). An existing password or key is kept
unless ROTATE=1. No secret is ever read from argv or the environment.

Files are rewritten in place, never renamed over: through Docker Desktop's file sharing a
container keeps reading the old file after a rename, even under a directory mount, and sees an
in-place write at once (measured by step S09, scripts/alerts.sh, and again here on a throwaway
Trino 484). Every change holds an exclusive flock on the file it rewrites (password.db for the
whole of `password`), so concurrent runs serialise; deploy's canary line (S19) must take the
same lock and write in place too. The new text is written by one pwrite, padded with newlines
to the old length (Trino 484 skips a blank line, measured; JSON skips whitespace), then
truncated, so a reader never meets a half-old line.
# ponytail: one pwrite is seen whole only within a page (4 KiB, about 50 password lines); past
# that, a reader racing the write could fail one parse and retry at Trino's next refresh.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import bcrypt
from k2gen import model
from k2gen.emit import api

PASSWORD_DB = "trino/password.db"
API_KEYS = "api/keys.json"
assert {PASSWORD_DB, API_KEYS} <= {f.path for f in model.SECRET_FILES}
BCRYPT_COST = 10  # the secrets table's cost; Trino refuses below 8


def die(message: str) -> None:
    sys.exit(f"principal-secret: {message}")


def secrets_root() -> Path:
    root = Path(os.environ.get(model.SECRETS_ENV) or Path.home() / "k2-secrets").absolute()
    probe = root
    while not probe.is_dir():
        probe = probe.parent
    git = subprocess.run(["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],
                         capture_output=True, text=True)
    if git.stdout.strip() == "true":
        die(f"{root} is inside a git checkout; {model.SECRETS_ENV} must name a directory outside any")
    return root


def known() -> tuple[set[str], set[str]]:
    """(every known principal, those k2-api may act as), from the working tree's documents."""
    documents: dict[str, bytes] = {}
    for root in model.DOCUMENT_ROOTS:
        base = model.REPO / root
        paths = sorted(base.rglob("*")) if root.endswith("/") else [base]
        documents |= {p.relative_to(model.REPO).as_posix(): p.read_bytes() for p in paths if p.is_file()}
    provenance = model.Provenance("git", "working-tree", "0" * 40, "principal-secret", "1970-01-01T00:00:00Z",
                                  model.set_sha256(documents), ())
    m = model.load(model.DocumentSet(documents, provenance))
    ids = {p.id for p in m.principals}
    ids |= {name(p.name) for p in m.products for name in (model.builder, model.publisher)
            if any(t.build.kind in ("dbt", "code") for t in p.tables)}
    callers = set(json.loads(api.emit(m)[model.API_PRINCIPALS])["principals"])
    return ids, callers


def directory(path: Path) -> Path:
    """`path`'s parent, created and kept at mode 700, as every directory up to the root is."""
    for d in reversed((path.parent, *path.parent.parents)):
        if not d.exists():
            d.mkdir(mode=0o700)
            os.chmod(d, 0o700)
    os.chmod(path.parent, 0o700)
    return path


@contextmanager
def locked(path: Path) -> Iterator[int]:
    """`path` open read-write, created at mode 600, under an exclusive flock until the block ends."""
    fd = os.open(directory(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        yield fd
    finally:
        os.close(fd)


def read(fd: int) -> str:
    return os.pread(fd, os.fstat(fd).st_size, 0).decode()


def write(fd: int, text: str) -> None:
    """Replace the content in place: one pwrite padded to the old length, then truncate."""
    new, old = text.encode(), os.fstat(fd).st_size
    padded = new.ljust(old, b"\n")
    if os.pwrite(fd, padded, 0) != len(padded):
        die("a short write; rerun with ROTATE=1")
    os.ftruncate(fd, len(new))
    os.fsync(fd)


def print_once(secret: str, what: str) -> None:
    print(secret, flush=True)
    print(f"principal-secret: that {what} is shown this once; store it now", file=sys.stderr)


def password(root: Path, principal: str, rotate: bool, to_file: bool) -> None:
    if to_file:
        try:
            model.password_mount(principal)
        except ValueError:
            die(f"{principal}: TO_FILE=1 is for a service principal; a person's password is printed, never stored")
    with locked(root / PASSWORD_DB) as db:
        lines = read(db).splitlines(keepends=True)
        mine = [i for i, line in enumerate(lines) if line.split(":", 1)[0] == principal]
        stored = root / model.password_file(principal)
        if stored.exists() and not to_file:  # else its containers would keep the old password
            die(f"{principal} has {stored}; TO_FILE=1 ROTATE=1 replaces both")
        if (mine or stored.exists()) and not rotate:
            die(f"{principal} already has a password; ROTATE=1 replaces it")
        secret = secrets.token_urlsafe(32)
        hashed = bcrypt.hashpw(secret.encode(), bcrypt.gensalt(BCRYPT_COST)).decode()
        line = f"{principal}:$2y${hashed.removeprefix('$2b$')}\n"  # the same hash; Trino reads only $2y$
        if mine:
            lines[mine[0]] = line
            lines = [x for i, x in enumerate(lines) if i not in mine[1:]]
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(line)
        if to_file:
            with locked(stored) as f:
                write(f, secret)
        write(db, "".join(lines))
    if to_file:
        print(f"principal-secret: {principal}: bcrypt line in {root / PASSWORD_DB}, password in {stored} (mode 600)",
              file=sys.stderr)
    else:
        print_once(secret, f"password for {principal}")


def api_key(root: Path, principal: str, rotate: bool) -> None:
    with locked(root / API_KEYS) as f:
        text = read(f).strip()
        keys: dict[str, str] = json.loads(text) if text else {}
        if principal in keys.values() and not rotate:
            die(f"{principal} already has an API key; ROTATE=1 replaces it")
        key = "k2_" + secrets.token_urlsafe(32)
        keys = {h: p for h, p in keys.items() if p != principal}
        keys[hashlib.sha256(key.encode()).hexdigest()] = principal
        write(f, json.dumps(keys, indent=2, sort_keys=True) + "\n")
    print_once(key, f"API key for {principal}")


def status(root: Path, ids: set[str], callers: set[str]) -> None:
    db, keys_file = root / PASSWORD_DB, root / API_KEYS
    with_password = {line.split(":", 1)[0] for line in db.read_text().splitlines() if line.strip()} if db.exists() else set()
    text = keys_file.read_text().strip() if keys_file.exists() else ""
    with_key = set(json.loads(text).values()) if text else set()
    print(f"{'principal':<28} {'password':<9} {'file':<5} api-key")
    for p in sorted(ids):
        has_file = (root / model.password_file(p)).exists() if not p.startswith("p.") else None
        print(f"{p:<28} {'yes' if p in with_password else 'no':<9} "
              f"{'-' if has_file is None else 'yes' if has_file else 'no':<5} "
              f"{('yes' if p in with_key else 'no') if p in callers else '-'}")
    canaries = sorted(u for u in with_password if u.startswith("k2_release_"))
    stray = sorted((with_password | with_key) - ids - set(canaries))
    if canaries:
        print(f"release canaries in {PASSWORD_DB} (deploy writes them): {', '.join(canaries)}")
    if stray:
        print(f"NOT DECLARED but still in {PASSWORD_DB} or {API_KEYS}: {', '.join(stray)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("password", "api-key", "status"))
    parser.add_argument("--principal", default="")
    parser.add_argument("--rotate", action="store_true", help="replace the principal's existing password or key")
    parser.add_argument("--to-file", action="store_true",
                        help="password of a service principal: write it to principals/<id>.password, print nothing")
    args = parser.parse_args()
    root = secrets_root()
    ids, callers = known()
    if args.action == "status":
        return status(root, ids, callers)
    if args.principal not in ids:
        die(f"{args.principal or 'PRINCIPAL=<id>'}: not a principal contracts/access/principals.yaml declares, "
            "nor a dbt or code product's builder or publisher")
    if args.action == "api-key" and args.principal not in callers:
        die(f"{args.principal}: k2-api never acts as it (compiled/api/principals.json), so it gets no key")
    if args.action == "api-key" and args.to_file:
        die("TO_FILE=1 is for a password; an API key is printed, never stored")
    if not args.to_file and not sys.stdout.isatty():
        die("stdout is not a terminal, and the secret is printed there once; run it in a terminal"
            + (", or TO_FILE=1 for a service principal's password file" if args.action == "password" else ""))
    if args.action == "password":
        password(root, args.principal, args.rotate, args.to_file)
    else:
        api_key(root, args.principal, args.rotate)


if __name__ == "__main__":
    main()
