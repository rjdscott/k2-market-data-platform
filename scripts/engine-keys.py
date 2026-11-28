#!/usr/bin/env python3
"""Writes each engine's MinIO key pair, and the host user's ids, into an env file.

    python3 scripts/engine-keys.py [--env-file FILE] [--rotate ENGINE[,ENGINE]]   # make engine-keys

The names: K2_<ENGINE>_S3_ACCESS_KEY and _SECRET_KEY for every policy file under
docker/lake/minio/policies/ (the engines lake-init makes a MinIO user for), and K2_RUN_UID and
K2_RUN_GID (os.getuid(), os.getgid()). Only a name that is missing, empty or still the value
.env.example ships is written, and both names of a pair that holds the root user or password (as
the release before phase 002 shipped Trino's); an engine named by --rotate gets a new secret whatever
it holds, under the same access key, so the next lake-init replaces that MinIO user's secret and the
old one stops working (a new access key would leave the old user, its policy and its secret valid).
An access key is k2-<engine>-<8 hex>, distinct from every other engine's and the root user; a
secret is 40 characters of secrets.token_urlsafe. Before a change the file is copied to
<file>.bak-<UTC time>, mode 600; the new file is written beside it at mode 600 and renamed over it
(no compose service bind-mounts the env file, so a rename strands no container's view of it).
Prints the names it wrote, never a value; a second run writes nothing.

A written pair reaches MinIO only when lake-init runs again, and a deploy runs it only when its
release changes lake-init, so a rotation is three steps: this, then `make lake-init` (lake-init on
the live release, as deploy runs it: MinIO takes the new secret and the old one stops working), then
the services that hold the engine's secret recreated so they read the new one, which
`make lake-init RECREATE="<service> ..."` does through the release (HOLDERS: trino for trino,
spark-iceberg for spark and maintenance; backup, restore and tools are passed theirs per run). Until lake-init
reruns MinIO keeps the old secret, and a holder recreated first is refused. The last lines printed
name the command. A first deploy runs lake-init itself.
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


def holders(compose: str) -> dict[str, list[str]]:
    """engine -> the long-running services compose hands its secret, lake-init aside: those a
    rotation must recreate. ponytail: a text scan of the top-level service keys, no YAML parser."""
    out: dict[str, set[str]] = {e: set() for e in ENGINES}
    service = None
    for line in compose.splitlines():
        if m := re.match(r"^  ([a-z0-9][a-z0-9-]*):\s*$", line):
            service = m[1]
        for e in ENGINES:
            if service not in (None, "lake-init") and "${" + pair_names(e)[1] in line:
                out[e].add(service)
    return {e: sorted(s) for e, s in out.items()}


HOLDERS = holders((ROOT / "docker-compose.yml").read_text())


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
        # A rotation keeps the access key: lake-init's `mc admin user add` then sets that user's
        # secret, so the old one stops working. A new key would leave the old user and policy valid.
        if on_root or unset(ak):
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
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog=__doc__.split("\n\n")[-1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--rotate", default="", help="engines whose secret is replaced, comma-separated")
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
    engines = [e for e in ENGINES if any(n in new for n in pair_names(e))]
    if engines:
        held = " ".join(sorted({s for e in engines for s in HOLDERS[e]}))
        command = f'make lake-init RECREATE="{held}"' if held else "make lake-init"
        print(f"engine-keys: next, on a deployed stack: {command} (lake-init gives MinIO the new secret"
              f"{', then ' + held + ' recreated to read it' if held else ''}); until then MinIO keeps the old"
              " secret. A first deploy runs lake-init itself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
