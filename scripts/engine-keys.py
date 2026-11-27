#!/usr/bin/env python3
"""Writes each engine's MinIO key pair, and the host user's ids, into an env file.

    python3 scripts/engine-keys.py [--env-file FILE] [--rotate ENGINE[,ENGINE]]   # make engine-keys

The names: K2_<ENGINE>_S3_ACCESS_KEY and _SECRET_KEY for every policy file under
docker/lake/minio/policies/ (the engines lake-init makes a MinIO user for), and K2_RUN_UID and
K2_RUN_GID (os.getuid(), os.getgid()). Only a name that is missing, empty or still the value
.env.example ships is written, and both names of a pair that holds the root user or password (as
the release before phase 002 shipped Trino's); an engine named by --rotate gets a new pair whatever
it holds.
An access key is k2-<engine>-<8 hex>, distinct from every other engine's and the root user; a
secret is 40 characters of secrets.token_urlsafe. Before a change the file is copied to
<file>.bak-<UTC time>, mode 600; the new file is written beside it at mode 600 and renamed over it
(no compose service bind-mounts the env file, so a rename strands no container's view of it).
Prints the names it wrote, never a value; a second run writes nothing. A new pair reaches MinIO
only when lake-init runs again (`make deploy` runs it, or `docker compose up lake-init`).
"""

import argparse
import os
import re
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINES = sorted(p.stem for p in (ROOT / "docker/lake/minio/policies").glob("*.json"))
LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def pair_names(engine: str) -> tuple[str, str]:
    return f"K2_{engine.upper()}_S3_ACCESS_KEY", f"K2_{engine.upper()}_S3_SECRET_KEY"


def values(text: str) -> dict[str, str]:
    """NAME -> value for every assignment line, the last one winning, as compose reads them.
    ponytail: quotes stripped, an inline comment kept; check-env.py reads the file through compose."""
    found = {}
    for line in text.splitlines():
        if m := LINE.match(line.strip()):
            found[m[1]] = m[2].strip().strip("'\"")
    return found


def plan(current: dict[str, str], shipped: dict[str, str], rotate: set[str], uid: int, gid: int) -> dict[str, str]:
    """The names to write and their new values. Pure, so a test can hand it dicts."""
    unset = lambda n: current.get(n, "") in ("", shipped.get(n))  # noqa: E731
    # A pair holding either half of the root pair is unset, both names of it: the release before
    # phase 002 shipped Trino on the root pair, and lake-init refuses one.
    root = {v for v in (current.get("MINIO_ROOT_USER"), current.get("MINIO_ROOT_PASSWORD")) if v}
    out = {}
    taken = {current.get("MINIO_ROOT_USER", "")} | {current.get(pair_names(e)[0], "") for e in ENGINES}
    for e in ENGINES:
        ak, sk = pair_names(e)
        on_root = bool(root & {current.get(ak), current.get(sk)})
        if on_root or e in rotate or unset(ak):
            while (key := f"k2-{e}-{secrets.token_hex(4)}") in taken:
                pass
            taken.add(key)
            out[ak] = key
        if on_root or e in rotate or unset(sk):
            out[sk] = secrets.token_urlsafe(30)  # 40 characters
    for name, value in (("K2_RUN_UID", uid), ("K2_RUN_GID", gid)):
        if unset(name):
            out[name] = str(value)
    return out


def rewrite(text: str, new: dict[str, str]) -> str:
    """Each line assigning a name in `new` takes its new value; names on no line are appended."""
    lines, seen = [], set()
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if m and m[1] in new:
            line = f"{m[1]}={new[m[1]]}"
            seen.add(m[1])
        lines.append(line)
    missing = [n for n in new if n not in seen]
    if missing:
        lines += ["", "# Written by scripts/engine-keys.py (make engine-keys)."] + [f"{n}={new[n]}" for n in missing]
    return "\n".join(lines) + "\n"


def write_600(path: Path, data: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--rotate", default="", help="engines whose pair is replaced, comma-separated")
    a = ap.parse_args(argv)
    path = Path(a.env_file)
    rotate = {e.strip().lower() for e in a.rotate.split(",") if e.strip()}
    if unknown := sorted(rotate - set(ENGINES)):
        print(f"engine-keys: no engine {', '.join(unknown)}; the engines are {', '.join(ENGINES)}", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"engine-keys: {path} does not exist: cp .env.example {path} first", file=sys.stderr)
        return 2
    text = path.read_text()
    new = plan(values(text), values((ROOT / ".env.example").read_text()), rotate, os.getuid(), os.getgid())
    if not new:
        print(f"engine-keys: nothing to write, every name in {path} is set")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup, tmp = path.with_name(f"{path.name}.bak-{stamp}"), path.with_name(f"{path.name}.engine-keys-{stamp}")
    write_600(backup, text)
    try:
        write_600(tmp, rewrite(text, new))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    for name in new:
        print(f"engine-keys: wrote {name}")
    print(f"engine-keys: {len(new)} names written to {path} (mode 600); the old file is {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
