"""A throwaway lake's env.json (scripts/lib/throwaway-lake.sh): written by write() and read by
read() and password(), here and nowhere else, so the lake and its readers (access_verify.py and
the scripts around it) cannot drift apart again, as they did once (the env.json of step S12 had
one `passwordFile`; S13's has a password file per user).

    {"release": "/work/releases/<id>", "trino": "https://trino:8443", "lakekeeper": "http://lakekeeper:8181",
     "secrets": "<this run's ${K2_SECRETS}>", "ca": "<secrets>/tls/ca.pem",
     "passwords": {"<user>": "<that user's password file>", ...}, "users": [<the users, sorted>],
     "warehouses": [<every warehouse, sorted>]}

Paths are the tools container's (/work is the lake's state directory). A password file holds
the password alone, mode 600; env.json holds only where it is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

KEYS = ("release", "trino", "lakekeeper", "secrets", "ca", "passwords", "users", "warehouses")


def write(path: Path, *, release: str, trino: str, lakekeeper: str, secrets: str, ca: str,
          passwords: dict[str, str], warehouses: list[str]) -> None:
    env = {"release": release, "trino": trino, "lakekeeper": lakekeeper, "secrets": secrets, "ca": ca,
           "passwords": dict(sorted(passwords.items())), "users": sorted(passwords), "warehouses": sorted(warehouses)}
    path.write_text(json.dumps(env, indent=2) + "\n")


def read(path: Path) -> dict[str, Any]:
    env = json.loads(path.read_text())
    if missing := [k for k in KEYS if k not in env]:
        raise ValueError(f"{path} lacks {', '.join(missing)}: not written by scripts/lib/lake_env.py")
    return env


def password(env: dict[str, Any], user: str) -> str:
    """`user`'s password: the file the lake's map names, else <secrets>/principals/<user>.password.
    KeyError, naming both, when neither is there."""
    for f in filter(None, (env["passwords"].get(user), f"{env['secrets']}/principals/{user}.password")):
        if Path(f).is_file():
            return Path(f).read_text().strip()
    raise KeyError(f"no password file for {user}: not in env.json's passwords, and no "
                   f"{env['secrets']}/principals/{user}.password")
