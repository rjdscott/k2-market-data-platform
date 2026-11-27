#!/usr/bin/env python3
"""Refuses to start the stack on a credential anyone can guess. `make up` and
scripts/dev-up.sh run it before `docker compose up`; a bare `docker compose up -d`
does not (SECURITY.md).

    python3 scripts/check-env.py [--env-file FILE]   # default: what compose reads, .env and the shell
    python3 scripts/check-env.py --mode-only [--env-file FILE]   # the file's mode alone

The file must be its owner's alone (mode 600 or tighter): it holds every secret of the stack,
Trino's internal shared secret included, with which a holder can pose as a Trino node (ADR-060).
A group- or world-readable or writable .env fails, the fix printed (`chmod 600 <file>`), here and
in every live-touching path that runs --mode-only (`make access-verify LIVE=1`).

The values are the ones compose will interpolate, read from
`docker compose config --environment`, so `export`, quotes, an inline comment,
CRLF endings, spaces around `=` and a variable exported in the shell resolve
exactly as compose resolves them. They are compared in-process and never
printed. Exits 1, naming each variable, when a secret in CREDENTIALS (every
password, secret and key compose passes to a service; tests/test_check_env.py
fails when compose or .env.example names one the table does not), or any
*_PASSWORD, *_SECRET or *_KEY .env.example lists (an *_ACCESS_KEY is the user
name an S3 secret key pairs with, not a secret), is:

- unset, empty, or still the value .env.example ships for it;
- equal to the user name it is paired with, ignoring case: a user name is not
  a secret (MinIO's is the S3 access key, which logs and consoles print);
- a known default (an image's factory password, a word typed in a hurry) or
  any other value .env.example ships;
- shorter than MIN_LENGTH characters.

Each engine's storage pair (K2_<ENGINE>_S3_ACCESS_KEY and _SECRET_KEY, one per policy file under
docker/lake/minio/policies/) is refused as lake-init refuses it: unset or still the placeholder,
equal to the root pair (the access key to MINIO_ROOT_USER, the secret to MINIO_ROOT_PASSWORD), or
an access key another engine also has.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# .env.example is ours and plain NAME=value, so a regex reads it.
SECRET = re.compile(r"^([A-Z0-9_]*(?:PASSWORD|SECRET|(?<!ACCESS_)KEY))=(.*)$", re.M)
SETTING = re.compile(r"^[A-Z0-9_]+=(.+)$", re.M)

# Every secret compose passes to a service, and the user it is paired with
# there: another variable ($NAME), the literal name the service uses, or None
# for a key with no user.
CREDENTIALS = {
    "CLICKHOUSE_PASSWORD": "default",           # ClickHouse's default user
    "K2_QUANT_PASSWORD": "quant",               # docker/clickhouse/users.xml
    "PREFECT_DB_PASSWORD": "$PREFECT_DB_USER",  # the Postgres superuser, which Lakekeeper also connects as
    "MINIO_ROOT_PASSWORD": "$MINIO_ROOT_USER",  # the S3 access key and secret
    "LAKEKEEPER_ENCRYPTION_KEY": None,
    "AIRFLOW_DB_PASSWORD": "airflow",           # docker/airflow/init-db.sh
    "AIRFLOW_JWT_SECRET": None,
    "AIRFLOW_API_SECRET_KEY": None,
    "GRAFANA_PASSWORD": "admin",                # GF_SECURITY_ADMIN_USER in docker-compose.yml
    "K2_TRINO_SHARED_SECRET": None,             # Trino's internal shared secret (ADR-060)
}
# The engines with a MinIO user: one definition, the policy files lake-init reads.
ENGINES = sorted(p.stem.upper() for p in (ROOT / "docker/lake/minio/policies").glob("*.json"))
CREDENTIALS |= {f"K2_{e}_S3_SECRET_KEY": f"$K2_{e}_S3_ACCESS_KEY" for e in ENGINES}  # each engine's S3 pair
# What .env.example tells you to generate clears this three times over
# (`openssl rand -hex 24` is 48 characters, `-base64 32` is 44), and 16 random
# hex characters are still 64 bits; a word, a name or a keyboard walk someone
# could guess is shorter. One floor for every secret, keys included.
MIN_LENGTH = 16
# Factory defaults of the images compose runs, and the words typed in a hurry.
# "prefect" is the database role's historical name (PREFECT_DB_USER, ADR-034).
KNOWN = {"admin", "administrator", "password", "passw0rd", "p@ssw0rd", "changeme", "change-me", "secret",
         "minioadmin", "minio123", "postgres", "prefect", "airflow", "clickhouse", "grafana", "default",
         "root", "toor", "test", "guest", "letmein", "qwerty", "123456", "12345678", "1234567890"}
FIX = "set it to `openssl rand -hex 24`"


def check(env: dict[str, str], example: str) -> list[str]:
    """Problems, by variable name only. Pure, so a test can hand it dicts."""
    placeholders = dict(SECRET.findall(example))
    shipped = {v.strip().lower() for v in SETTING.findall(example)}
    problems = []
    for name in [*CREDENTIALS, *(n for n in placeholders if n not in CREDENTIALS)]:
        value, user = env.get(name, ""), CREDENTIALS.get(name)
        user_value = env.get(user[1:], "") if user and user.startswith("$") else user
        if value in ("", placeholders.get(name)):
            problems.append(f"{name} is unset, empty or still the .env.example placeholder")
        elif user_value and value.lower() == user_value.lower():
            label = user[1:] if user.startswith("$") else f"its user name `{user}`"
            problems.append(f"{name} equals {label}; {FIX}")
        elif value.lower() in KNOWN or value.strip().lower() in shipped:
            problems.append(f"{name} is a known default or a value .env.example ships; {FIX}")
        elif len(value) < MIN_LENGTH:
            problems.append(f"{name} is shorter than {MIN_LENGTH} characters; {FIX}")
    return problems + scoped_problems(env, example)


def scoped_problems(env: dict[str, str], example: str) -> list[str]:
    """lake-init's three refusals of an engine's pair (docker/lake/init-lake.sh), by name only. A
    secret unset or a placeholder is check()'s; the access key's own placeholder is here."""
    shipped = dict(re.findall(r"^(K2_[A-Z]+_S3_ACCESS_KEY)=(.*)$", example, re.M))
    problems, owner = [], {}
    for e in ENGINES:
        ak, sk = f"K2_{e}_S3_ACCESS_KEY", f"K2_{e}_S3_SECRET_KEY"
        access = env.get(ak, "")
        if access in ("", shipped.get(ak)):
            problems.append(f"{ak} is unset, empty or still the .env.example placeholder; `make engine-keys` writes it")
            continue
        if access == env.get("MINIO_ROOT_USER"):
            problems.append(f"{ak} equals MINIO_ROOT_USER: {e.lower()} gets its own MinIO user, never the root's")
        if env.get(sk) and env.get(sk) == env.get("MINIO_ROOT_PASSWORD"):
            problems.append(f"{sk} equals MINIO_ROOT_PASSWORD: {e.lower()} gets its own MinIO user, never the root's")
        if access in owner:
            problems.append(f"{ak} is also {owner[access]}'s access key: one MinIO user per engine")
        owner.setdefault(access, ak)
    return problems


def mode_problem(path: Path) -> str | None:
    """Why `path` is open to anyone but its owner, or None (also when it does not exist)."""
    if not path.exists():
        return None
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        return f"{path} is mode {mode:03o}, open to its group or others, and it holds the stack's secrets: chmod 600 {path}"
    return None


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
    ap.add_argument("--mode-only", action="store_true", help="check the file's mode and nothing else")
    a = ap.parse_args()
    where = a.env_file or ".env"
    if loose := mode_problem(Path(a.env_file) if a.env_file else ROOT / ".env"):
        print(f"check-env: FAIL {loose}", file=sys.stderr)
        return 1
    if a.mode_only:
        print(f"check-env: PASS {where} is its owner's alone (or absent)")
        return 0
    env = compose_environment(a.env_file)
    if env is None:
        print(f"check-env: FAIL {where}: `docker compose config --environment` failed", file=sys.stderr)
        return 1
    problems = check(env, (ROOT / ".env.example").read_text())
    for p in problems:
        print(f"check-env: FAIL {where}: {p}", file=sys.stderr)
    if not problems:
        print(f"check-env: PASS {where} as compose resolves it (no secret unset, a placeholder, its user, a known default, or under {MIN_LENGTH} characters; "
              f"each of {len(ENGINES)} engines' storage pairs set, not the root pair, not shared)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
