"""Shared test setup: a releases root holding the compiled release of tests/release/, with the
k2-api pointer on it, as deploy lays it out (`/k2/releases/pointers/k2-api` -> `../<id>`), and a
key map as `make api-key` writes it ({sha256 hex of the whole key: principal})."""

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from k2api import app as api
from k2api import db
from k2api import keys as api_keys

(SOURCE,) = (p for p in (Path(__file__).parent / "release").iterdir() if p.is_dir())
RELEASE_ID = SOURCE.name
# Made-up keys in `make api-key`'s form, `k2_` and 43 url-safe characters.
FULL = "k2_" + "F" * 43  # p.analyst-full, in the release's principals.json
DELAYED = "k2_" + "d" * 43  # p.analyst-delayed, in it too
UNSERVED = "k2_" + "u" * 43  # p.not-served: in keys.json, not in principals.json
SERVICE = "k2_" + "s" * 43  # svc.api itself: never a caller
KEYS = {FULL: "p.analyst-full", DELAYED: "p.analyst-delayed", UNSERVED: "p.not-served", SERVICE: "svc.api"}


def write_keys(path: Path, pairs: dict[str, str]) -> None:
    path.write_text(json.dumps({hashlib.sha256(k.encode()).hexdigest(): p for k, p in pairs.items()}))


@pytest.fixture
def keys_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "api" / "keys.json"
    path.parent.mkdir()
    write_keys(path, KEYS)
    monkeypatch.setattr(api_keys, "KEYS_FILE", str(path))
    monkeypatch.setattr(api_keys, "_seen", {"stamp": None, "keys": {}})
    return path


@pytest.fixture
def releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "releases"
    shutil.copytree(SOURCE, root / RELEASE_ID)
    (root / "pointers").mkdir()
    (root / "pointers" / "k2-api").symlink_to(f"../{RELEASE_ID}")
    monkeypatch.setattr(api, "RELEASES", root)
    return root


@pytest.fixture
def client(releases: Path, keys_file: Path) -> TestClient:
    return TestClient(api.app)


class FakeTrino:
    """Trino at the one seam, db.run, enforcing the release's own compiled rules: a user other
    than svc.api runs only if an impersonation rule lets svc.api become it, and reads a relation
    only by the first table rule naming it, by user or by a group of groups.txt (default deny;
    row filters and masks are Trino's and not modelled). Records (user, sql, params) per call."""

    def __init__(self, rel: Path) -> None:
        self.rules = json.loads((rel / "compiled/trino/rules.json").read_text())
        self.groups: dict[str, set[str]] = {}
        for line in (rel / "compiled/trino/groups.txt").read_text().split():
            group, _, members = line.partition(":")
            for m in members.split(","):
                self.groups.setdefault(m, set()).add(group)
        self.calls: list[tuple[str, str, list[Any]]] = []
        self.rows: dict[str, list[dict[str, Any]]] = {}  # relation -> rows
        self.snapshots: dict[str, list[int]] = {}  # base relation -> the id of each read in turn; the last repeats
        self.down = False

    def _matches(self, rule: dict[str, Any], user: str) -> bool:
        if "user" in rule:
            return bool(re.fullmatch(rule["user"], user))
        if "group" in rule:
            return any(re.fullmatch(rule["group"], g) for g in self.groups.get(user, ()))
        return True

    def may_select(self, user: str, catalog: str, schema: str, table: str) -> bool:
        for rule in self.rules["tables"]:
            where = all(re.fullmatch(rule.get(k, ".*"), v) for k, v in (("catalog", catalog), ("schema", schema), ("table", table)))
            if self._matches(rule, user) and where:
                return "SELECT" in rule["privileges"]
        return False

    def __call__(self, user: str, sql: str, params: list[Any]) -> list[list[Any]]:
        self.calls.append((user, sql, list(params)))
        if self.down:
            raise db.Unavailable("ConnectionError")
        if user != db.API_USER and not any(re.fullmatch(r["original_user"], db.API_USER) and re.fullmatch(r["new_user"], user)
                                           and r["allow"] for r in self.rules["impersonation"]):
            raise db.Denied()
        catalog, schema, table = re.search(r' FROM "([a-z0-9_]+)"\."([a-z0-9_]+)"\."([a-z0-9_$]+)"', sql).groups()
        if not self.may_select(user, catalog, schema, table):
            raise db.Denied()
        if table.endswith("$refs"):
            ids = self.snapshots.get(f"{catalog}.{schema}.{table.removesuffix('$refs')}", [])
            return [[ids.pop(0) if len(ids) > 1 else ids[0]]] if ids else []
        columns = re.findall(r'"([a-z0-9_]+)"', sql.split(" FROM ")[0])
        order = re.search(r" ORDER BY (.+) LIMIT", sql)
        key = re.findall(r'"([a-z0-9_]+)"', order.group(1)) if order else []
        rows = sorted(self.rows.get(f"{catalog}.{schema}.{table}", []), key=lambda row: [row[k] for k in key])
        limit = int(re.search(r" LIMIT ([0-9]+)$", sql).group(1))
        return [[row[c] for c in columns] for row in rows][:limit]


@pytest.fixture(autouse=True)
def trino(releases: Path, monkeypatch: pytest.MonkeyPatch) -> FakeTrino:
    """Every test: no statement reaches a real Trino."""
    fake = FakeTrino(releases / RELEASE_ID)
    monkeypatch.setattr(db, "run", fake)
    return fake


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}
