"""Shared test setup: a releases root holding the compiled release of tests/release/, with the
k2-api pointer on it, as deploy lays it out (`/k2/releases/pointers/k2-api` -> `../<id>`)."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from k2api import app as api

(SOURCE,) = (p for p in (Path(__file__).parent / "release").iterdir() if p.is_dir())
RELEASE_ID = SOURCE.name


@pytest.fixture
def releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "releases"
    shutil.copytree(SOURCE, root / RELEASE_ID)
    (root / "pointers").mkdir()
    (root / "pointers" / "k2-api").symlink_to(f"../{RELEASE_ID}")
    monkeypatch.setattr(api, "RELEASES", root)
    return root


@pytest.fixture
def client(releases: Path) -> TestClient:
    return TestClient(api.app)
