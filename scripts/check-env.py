#!/usr/bin/env python3
"""Refuses to start the stack on a credential anyone can guess. `make up` and
scripts/dev-up.sh run it before `docker compose up`; a bare `docker compose up -d`
does not (SECURITY.md).

    python3 scripts/check-env.py [--env-file FILE]   # default: what compose reads, .env and the shell

The values are the ones compose will interpolate, read from
`docker compose config --environment`, so `export`, quotes, an inline comment,
CRLF endings, spaces around `=` and a variable exported in the shell resolve
exactly as compose resolves them. They are compared in-process and never
printed. Exits 1, naming each variable, when a secret in CREDENTIALS (every
password, secret and key compose passes to a service; tests/test_check_env.py
fails when compose or .env.example names one the table does not), or any
*_PASSWORD, *_SECRET or *_KEY .env.example lists, is:

- unset, empty, or still the value .env.example ships for it;
- equal to the user name it is paired with, ignoring case: a user name is not
  a secret (MinIO's is the S3 access key, which logs and consoles print);
- a known default (an image's factory password, a word typed in a hurry) or
  any other value .env.example ships;
- shorter than MIN_LENGTH characters.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# .env.example is ours and plain NAME=value, so a regex reads it.
SECRET = re.compile(r"^([A-Z0-9_]*(?:PASSWORD|SECRET|KEY))=(.*)$", re.M)
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
}
# What .env.example tells you to generate clears this three times over
# (`openssl rand -hex 24` is 48 characters, `-base64 32` is 44), and 16 random
# hex characters are still 64 bits; a word, a name or a keyboard walk someone
# could guess is shorter. One floor for every secret, keys included.
MIN_LENGTH = 16
# Factory defaults of the images compose runs, and the words typed in a hurry.
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
        print(f"check-env: PASS {where} as compose resolves it (no secret unset, a placeholder, its user, a known default, or under {MIN_LENGTH} characters)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
