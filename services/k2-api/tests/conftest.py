"""Shared test setup: a releases root holding the compiled release of tests/release/, with the
k2-api pointer on it, as deploy lays it out (`/k2/releases/pointers/k2-api` -> `../<id>`), and a
key map as `make api-key` writes it ({sha256 hex of the whole key: principal})."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from k2api import app as api
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


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}
