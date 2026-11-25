#!/usr/bin/env python3
"""Refuses to start the stack on a credential anyone can guess. `make up` and
scripts/dev-up.sh run it before `docker compose up`; a bare `docker compose up -d`
does not (SECURITY.md).

    python3 scripts/check-env.py [--env-file FILE]   # default: what compose reads, .env and the shell

The values are the ones compose will interpolate, read from
`docker compose config --environment`, so `export`, quotes, an inline comment,
CRLF endings, spaces around `=` and a variable exported in the shell resolve
exactly as compose resolves them. They are compared in-process and never
printed. Exits 1, naming each variable, when:

- MINIO_ROOT_USER equals MINIO_ROOT_PASSWORD. The user is MinIO's access key,
  which logs and consoles print, so a password equal to it is no password;
- a variable .env.example lists whose name ends in PASSWORD, SECRET or KEY is
  unset, empty, or still the value .env.example ships for it.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# .env.example is ours and plain NAME=value, so a regex reads it.
SECRET = re.compile(r"^([A-Z0-9_]*(?:PASSWORD|SECRET|KEY))=(.*)$", re.M)


def check(env: dict[str, str], example: str) -> list[str]:
    """Problems, by variable name only. Pure, so a test can hand it dicts."""
    problems = []
    if env.get("MINIO_ROOT_USER", "") == env.get("MINIO_ROOT_PASSWORD", ""):
        problems.append("MINIO_ROOT_USER equals MINIO_ROOT_PASSWORD; set the password to `openssl rand -hex 24`")
    for name, placeholder in SECRET.findall(example):
        if env.get(name, "") in ("", placeholder):
            problems.append(f"{name} is unset, empty or still the .env.example placeholder")
    return problems


def compose_environment(env_file: str | None) -> dict[str, str] | None:
    args = ["docker", "compose", *(["--env-file", env_file] if env_file else []), "config", "--environment"]
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        return None  # its stderr is not printed: it can quote the file
    # ponytail: one NAME=value per line; a quoted value spanning lines is not parsed.
    return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env-file")
    a = ap.parse_args()
    where = a.env_file or ".env"
    env = compose_environment(a.env_file)
    if env is None:
        print(f"check-env: FAIL {where}: `docker compose config --environment` failed", file=sys.stderr)
        return 1
    problems = check(env, (ROOT / ".env.example").read_text())
    for p in problems:
        print(f"check-env: FAIL {where}: {p}", file=sys.stderr)
    if not problems:
        print(f"check-env: PASS {where} as compose resolves it (MinIO user and password differ; no placeholder secrets)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
