#!/usr/bin/env python3
"""Creates, rotates or removes one principal's Trino password or k2-api key; writes only its hash.

    make password PRINCIPAL=<id> [ROTATE=1] [TO_FILE=1]
    make api-key PRINCIPAL=<id> [ROTATE=1]
    make principal-remove PRINCIPAL=<id> [FORCE=1]
    make principals-status

The procedure around it: docs/runbooks/create-or-rotate-a-principal.md. Every file is under
${K2_SECRETS:-$HOME/k2-secrets}, laid out as k2gen.model.SECRET_FILES says, outside any git
checkout (refused inside one, found by a `.git` above it, never by asking git), and nothing here
is ever committed, compiled or hashed into a release. Every run puts ${K2_SECRETS} and its
trino/, api/ and principals/ back to mode 700, and each file it writes to 600.

- `password`: a 32-byte random secret (secrets.token_urlsafe, 43 characters). Its bcrypt, cost
  10 and spelt `$2y$` as Trino's file authenticator requires, replaces or appends the one line
  `<id>:<hash>` of trino/password.db; every other line, the release canary's included, is kept
  byte for byte and in place. The secret is printed once on stdout, which must be a terminal.
  With TO_FILE=1 it is instead written to principals/<id>.password, mode 600, and printed
  nowhere: for a service principal (dbt_*, pub_*, svc.*), for the containers that mount it
  (model.password_mount), and for a test principal, a person granted a role on the access
  canary and on no other product (access_verify.platform_held), whose password the platform
  holds so that `make access-verify LIVE=1` can run its rows. A person with a grant on any
  other product is refused: their password is theirs, printed once.
- `api-key`: `k2_` and a 32-byte random secret, printed once on stdout, which must be a
  terminal. api/keys.json maps the sha256 hex of the whole key, `k2_` included, to the
  principal; a rotation drops the principal's old key. Only a principal k2-api may act as
  (compiled/api/principals.json, k2gen.emit.api) gets one.
- `remove`: drops the principal's lines of trino/password.db, its entries of api/keys.json, and
  principals/<id>.password (emptied in place, then unlinked, so a container's stale mount of it
  reads nothing); every other line and entry is kept byte for byte. It prints the names of what
  it removed, or that there was nothing, and exits 0 either way: the state asked for holds, so a
  rerun is not an error. Refused for a principal still known unless FORCE=1, and always for the
  release canary.
- `status`: each known principal's id and which of the three it has, then a `STALE` line for
  each principal holding any of the three that is not known, and a `MALFORMED` line for each
  line of password.db Trino would refuse (by line number and reason, never its content); never
  a hash or a secret. Exit 1 when any STALE or MALFORMED line is printed.

A principal is known when contracts/access/principals.yaml declares it or it is the builder or
publisher of a dbt or code product (model.builder, model.publisher): anything else is refused,
the release canary included (deploy writes its line, S19), and `remove` refuses a known one unless
FORCE=1 and the canary always. An existing password or key is kept
unless ROTATE=1. No secret is ever read from argv or the environment.

Files are rewritten in place, never renamed over: through Docker Desktop's file sharing a
container keeps reading the old file after a rename, even under a directory mount, and sees an
in-place write at once (measured by step S09, scripts/alerts.sh, and again here on a throwaway
Trino 484). Every change holds an exclusive flock on the file it rewrites (password.db for the
whole of `password`), so concurrent runs serialise; deploy's canary line (S19) takes the same
lock and the same write() (scripts/deploy.py).

password.db is the one file a reader rereads while it is written (Trino, every 5 s), and one bad
line makes Trino answer every login HTTP 500. So write() never shifts it: each line is a fixed
slot of SLOT bytes, `<user>:<hash>` padded with spaces (Trino 484 trims a line and skips an
empty one: blank, space, tab and NUL lines measured; a `#` line is refused), 32 slots to a
4 KiB page so none straddles one. A rotation rewrites its own slot at the same offset, a removal
overwrites it with spaces, an addition takes a free slot or extends the file by one blank slot
and then fills it: every change is one aligned pwrite of one slot, the file never shrinks, and
no other line moves. A reader that races a write sees each slot old or new, never another
user's hash beside a name. Before writing it checks the whole current file and the whole new
one (each line `<user>:$2y$10$<53>`, each user once, problems()) and refuses either, naming the
line and the reason, unless the bad lines are the ones being removed; after writing it reads the
file back and checks it again. A file not yet in slots (written by hand, or before this layout)
is rewritten into slots once: one pwrite padded to the old length, then a truncate. The other
files are small and read at start or per request: the same one padded pwrite and truncate.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import sys
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path

import bcrypt
from k2gen import model
from k2gen.emit import api

PASSWORD_DB = "trino/password.db"
API_KEYS = "api/keys.json"
assert {PASSWORD_DB, API_KEYS} <= {f.path for f in model.SECRET_FILES}
MANAGED_DIRS = ("trino", "api", "principals")  # under ${K2_SECRETS}, kept at mode 700 on every run
BCRYPT_COST = 10  # the secrets table's cost; Trino refuses below 8
SLOT = 128  # bytes per password.db line, its newline included: 32 to a 4 KiB page
USER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
HASH = re.compile(r"\$2y\$10\$[./A-Za-z0-9]{53}")  # what this script and deploy write
MAX_USER = SLOT - 1 - len(":") - 60  # a user name longer than this fits no slot
TRIM = "".join(map(chr, range(33)))  # what Java's String.trim() strips, NUL to space: Trino trims each line
BLANK = " " * (SLOT - 1) + "\n"  # a free slot, which Trino skips


def die(message: str) -> None:
    sys.exit(f"principal-secret: {message}")


def checkout_above(path: Path) -> Path | None:
    """The first directory at or above `path` holding `.git` (a checkout's directory or a
    worktree's file), found without calling git: git refuses a repository another uid owns
    ("dubious ownership") and prints nothing, which must not read as "not a checkout". A
    directory that cannot be looked into counts as a checkout: the caller refuses."""
    probe = path
    try:
        while not probe.exists():
            probe = probe.parent
        probe = probe.resolve(strict=True)
    except OSError:
        return probe
    for d in (probe, *probe.parents):
        try:
            (d / ".git").lstat()
            return d
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            return d
    return None


def secrets_root() -> Path:
    root = Path(os.environ.get(model.SECRETS_ENV) or Path.home() / "k2-secrets").absolute()
    if (checkout := checkout_above(root)) is not None:
        die(f"{root} is inside a git checkout ({checkout}), or a directory above it cannot be looked into; "
            f"{model.SECRETS_ENV} must name a directory outside any")
    return root


def keep_modes(root: Path) -> None:
    """${K2_SECRETS} and the directories this script manages back to mode 700, on every run: a
    directory loosened by hand, or made by Docker for a mount (mode 755), does not stay so."""
    for d in (root, *(root / name for name in MANAGED_DIRS)):
        if d.is_dir() and not d.is_symlink():
            os.chmod(d, 0o700)


def known() -> tuple[set[str], set[str], set[str]]:
    """(every known principal, those k2-api may act as, the test principals whose password the
    platform holds), from the working tree's documents."""
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
    # The live suite's own rule for whose password the platform holds, so the two cannot differ.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from access_verify import platform_held
    return ids, callers, platform_held(m)


def directory(path: Path) -> Path:
    """`path`'s parent, created and kept at mode 700, as every directory up to the root is."""
    for d in reversed((path.parent, *path.parent.parents)):
        if not d.exists():  # exist_ok: a concurrent run may make it between the check and the mkdir
            d.mkdir(mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)
    os.chmod(path.parent, 0o700)
    return path


_OPEN: dict[int, Path] = {}  # each fd locked() holds open, to its path: write() knows password.db by it


@contextmanager
def locked(path: Path) -> Iterator[int]:
    """`path` open read-write, created at mode 600, under an exclusive flock until the block ends."""
    fd = os.open(directory(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        _OPEN[fd] = path
        yield fd
    finally:
        _OPEN.pop(fd, None)
        os.close(fd)


def read(fd: int) -> str:
    return os.pread(fd, os.fstat(fd).st_size, 0).decode()


def write(fd: int, text: str) -> None:
    """Replace the content in place. password.db (by its name, from locked()) slot by slot,
    checked before and after (write_db); any other file by one pwrite padded to the old length,
    then a truncate."""
    if fd in _OPEN and _OPEN[fd].name == Path(PASSWORD_DB).name:
        return write_db(fd, text)
    rewrite(fd, text.encode())


def rewrite(fd: int, new: bytes) -> None:
    padded = new.ljust(os.fstat(fd).st_size, b"\n")
    if os.pwrite(fd, padded, 0) != len(padded):
        die("a short write; rerun with ROTATE=1")
    os.ftruncate(fd, len(new))
    os.fsync(fd)


def lines_of(text: str) -> list[str]:
    """The lines as Java's readAllLines splits them (at LF, CRLF or CR), each trimmed as Trino trims it."""
    return [x.strip(TRIM) for x in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def problems(text: str) -> list[tuple[int, str | None, str]]:
    """Each line of a password.db that Trino 484 would refuse, making every login HTTP 500, as
    (line number, its user when that is a well-formed name, why). Never the line's content: a
    line that is not `<user>:<hash>` may hold anything, a pasted password included."""
    found: list[tuple[int, str | None, str]] = []
    first: dict[str, int] = {}
    for n, line in enumerate(lines_of(text), 1):
        if not line:
            continue
        user, colon, hashed = line.partition(":")
        if not colon:
            found.append((n, None, "no `:` between a user and a hash"))
        elif not USER.fullmatch(user) or len(user) > MAX_USER:
            found.append((n, None, f"the user is not a principal id of at most {MAX_USER} characters"))
        elif not HASH.fullmatch(hashed):
            found.append((n, user, f"{user}'s hash is not a cost-10 `$2y$` bcrypt"))
        elif user in first:
            found.append((n, user, f"{user} again (first at line {first[user]})"))
        else:
            first[user] = n
    return found


def entries(text: str) -> dict[str, str]:
    """password.db's users to their hashes, in file order; only for a text problems() passes."""
    return dict(line.split(":", 1) for line in lines_of(text) if line)


def slot(user: str, hashed: str) -> bytes:
    return (f"{user}:{hashed}".ljust(SLOT - 1) + "\n").encode()


def checked(old: str, text: str) -> dict[str, str]:
    """`text`'s users and hashes, once both the new password.db and the current one parse, but
    for the current lines of users the new one drops (a removal is the repair); else refused."""
    if bad := problems(text):
        die(f"refused: the new {PASSWORD_DB} would not parse and Trino would refuse every login: "
            + "; ".join(f"line {n}: {why}" for n, _, why in bad))
    want = entries(text)
    if bad := [(n, why) for n, user, why in problems(old) if user is None or user in want]:
        die(f"refused: {PASSWORD_DB} has lines Trino refuses, so every login fails now: "
            + "; ".join(f"line {n}: {why}" for n, why in bad)
            + "; make principal-remove PRINCIPAL=<that user> repairs a user's line, and "
            "docs/runbooks/create-or-rotate-a-principal.md section 4 any other")
    return want


def write_db(fd: int, text: str) -> None:
    """password.db becomes `text`'s users and hashes without moving a line (the module's docstring)."""
    old = read(fd)
    want = checked(old, text)
    slots = [old[i:i + SLOT] for i in range(0, len(old), SLOT)]
    if not (old.isascii() and all(len(s) == SLOT and s.find("\n") == SLOT - 1 for s in slots)):
        rewrite(fd, b"".join(slot(u, h) for u, h in want.items()))  # not yet in slots: once
    else:
        where: dict[str, list[int]] = {}
        for i, s in enumerate(slots):
            if user := s.strip(TRIM).partition(":")[0]:
                where.setdefault(user, []).append(i)
        free = [i for i, s in enumerate(slots) if not s.strip(TRIM)]
        for user, at in where.items():
            for i in at if user not in want else ():
                put(fd, i, BLANK.encode())
                free.append(i)
            if user in want and slots[at[0]].strip(TRIM) != f"{user}:{want[user]}":
                put(fd, at[0], slot(user, want[user]))
        for user in (u for u in want if u not in where):
            if free:
                i = free.pop(0)
            else:  # one blank slot past the end first, then filled: a reader meets blank or whole
                i = os.fstat(fd).st_size // SLOT
                put(fd, i, BLANK.encode())
            put(fd, i, slot(user, want[user]))
    os.fsync(fd)
    after = read(fd)
    if problems(after) or entries(after) != want:
        die(f"{PASSWORD_DB} does not read back as written; rerun")


def put(fd: int, i: int, data: bytes) -> None:
    """One slot, by one pwrite at its own offset."""
    assert len(data) == SLOT
    if os.pwrite(fd, data, i * SLOT) != SLOT:
        die(f"a short write to {PASSWORD_DB}; rerun")


def print_once(secret: str, what: str) -> None:
    print(secret, flush=True)
    print(f"principal-secret: that {what} is shown this once; store it now", file=sys.stderr)


def password(root: Path, principal: str, rotate: bool, to_file: bool, held: frozenset[str] = frozenset()) -> None:
    """`held`: the test principals whose password the platform holds (known()), who may take TO_FILE=1."""
    if to_file and principal not in held:
        try:
            model.password_mount(principal)
        except ValueError:
            die(f"{principal}: TO_FILE=1 is for a service principal, or a test principal granted on the access canary "
                "and nothing else; a person's password is printed, never stored")
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
        checked(read(db), "".join(lines))  # refused here, before the password file changes
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


def remove(root: Path, principal: str) -> None:
    db, keys_file, stored = root / PASSWORD_DB, root / API_KEYS, root / model.password_file(principal)
    removed = []
    with (locked(db) if db.exists() else nullcontext()) as f:  # held over the file, as password() holds it
        if f is not None:
            lines = read(f).splitlines(keepends=True)
            kept = [line for line in lines if line.split(":", 1)[0] != principal]
            if len(kept) < len(lines):
                write(f, "".join(kept))
                removed.append(f"{len(lines) - len(kept)} line(s) of {PASSWORD_DB}")
        if stored.is_file():
            with locked(stored) as s:
                write(s, "")  # a container's stale mount of it then reads nothing, not the password
                stored.unlink()
            removed.append(model.password_file(principal))
    if keys_file.exists():
        with locked(keys_file) as f:
            text = read(f).strip()
            keys: dict[str, str] = json.loads(text) if text else {}
            kept_keys = {h: p for h, p in keys.items() if p != principal}
            if len(kept_keys) < len(keys):
                write(f, json.dumps(kept_keys, indent=2, sort_keys=True) + "\n")
                removed.append(f"{len(keys) - len(kept_keys)} key(s) of {API_KEYS}")
    print(f"principal-secret: {principal}: "
          + (f"removed {', '.join(removed)}" if removed else "nothing to remove") + f" under {root}", file=sys.stderr)


def status(root: Path, ids: set[str], callers: set[str], held: set[str]) -> int:
    """The table and its STALE and MALFORMED lines; 1 when there is any of either."""
    db, keys_file = root / PASSWORD_DB, root / API_KEYS
    db_text = db.read_text() if db.exists() else ""
    malformed = problems(db_text)
    nameless = {n for n, user, _ in malformed if user is None}  # its content is never printed, not even as a name
    with_password = {line.partition(":")[0] for n, line in enumerate(lines_of(db_text), 1) if line and n not in nameless}
    text = keys_file.read_text().strip() if keys_file.exists() else ""
    with_key = set(json.loads(text).values()) if text else set()
    with_file = {p.name.removesuffix(".password") for p in root.glob(model.password_file("*"))}
    print(f"{'principal':<28} {'password':<9} {'file':<5} api-key")
    for p in sorted(ids):
        has_file = (root / model.password_file(p)).exists() if not p.startswith("p.") or p in held else None
        print(f"{p:<28} {'yes' if p in with_password else 'no':<9} "
              f"{'-' if has_file is None else 'yes' if has_file else 'no':<5} "
              f"{('yes' if p in with_key else 'no') if p in callers else '-'}")
    canaries = sorted(u for u in with_password if u.startswith("k2_release_"))
    stale = sorted((with_password | with_key | with_file) - ids - set(canaries))
    if canaries:
        print(f"release canaries in {PASSWORD_DB} (deploy writes them): {', '.join(canaries)}")
    for p in stale:
        where = [f for f, has in ((PASSWORD_DB, p in with_password), (API_KEYS, p in with_key),
                                  (model.password_file(p), p in with_file)) if has]
        why = "neither declared nor a dbt or code product's builder or publisher"
        if p in with_password:
            why += ", yet Trino still logs it in"
            if any(re.fullmatch(r, p) for r in model.PIPELINE_PRINCIPALS):
                why += " and the platform's pattern rules still match it"
        print(f"STALE {p} in {', '.join(where)}: {why}; make principal-remove PRINCIPAL={p}")
    for n, user, why in malformed:
        fix = f"make principal-remove PRINCIPAL={user} FORCE=1" if user else \
            "blank it in place (docs/runbooks/create-or-rotate-a-principal.md section 4)"
        print(f"MALFORMED {PASSWORD_DB} line {n}: {why}; Trino refuses every login while it stands; {fix}")
    return 1 if stale or malformed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("action", choices=("password", "api-key", "remove", "status"))
    parser.add_argument("--principal", default="")
    parser.add_argument("--rotate", action="store_true", help="replace the principal's existing password or key")
    parser.add_argument("--to-file", action="store_true",
                        help="password of a service or test principal: write it to principals/<id>.password, print nothing")
    parser.add_argument("--force", action="store_true", help="remove: the credentials of a principal still known")
    args = parser.parse_args()
    root = secrets_root()
    keep_modes(root)
    ids, callers, held = known()
    if args.action == "status":
        sys.exit(status(root, ids, callers, held))
    if args.action == "remove":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.principal):
            die(f"{args.principal or 'PRINCIPAL=<id>'}: not a principal id")
        if args.principal.startswith("k2_release_"):
            die(f"{args.principal}: a release canary; deploy owns its line (S19)")
        if args.principal in ids and not args.force:
            die(f"{args.principal} is still known (declared in contracts/access/principals.yaml, or a dbt or code "
                "product's builder or publisher); undeclare it first, or FORCE=1 removes its credentials anyway")
        return remove(root, args.principal)
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
        password(root, args.principal, args.rotate, args.to_file, frozenset(held))
    else:
        api_key(root, args.principal, args.rotate)


if __name__ == "__main__":
    main()
