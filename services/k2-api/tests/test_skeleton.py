"""W3-1: /healthz, /v1/release through the pointer, /v1/openapi.json from the frozen file."""

import json
import shutil
from pathlib import Path

import yaml
from conftest import RELEASE_ID
from fastapi.testclient import TestClient

from k2api import app as api

FROZEN = Path(__file__).parent.parent / "openapi.yaml"


def test_healthz_reaches_nothing_but_the_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api, "RELEASES", tmp_path / "nothing")  # no release at all
    r = TestClient(api.app).get("/healthz")
    assert (r.status_code, r.json()) == (200, {"status": "ok"})


def test_release_is_the_manifest_of_the_release_the_pointer_names(client: TestClient, releases: Path) -> None:
    manifest = json.loads((releases / RELEASE_ID / "manifest.json").read_text())
    r = client.get("/v1/release")
    assert r.status_code == 200
    assert r.json() == {k: manifest[k] for k in ("release", "tag", "commit", "builtAt")}
    assert r.json()["release"] == RELEASE_ID


def test_a_deploy_is_seen_at_the_next_request_without_a_restart(client: TestClient, releases: Path) -> None:
    other = "v3.0.0-alpha.3-rc.2-0123456789ab"
    shutil.copytree(releases / RELEASE_ID, releases / other)
    manifest = json.loads((releases / other / "manifest.json").read_text()) | {"release": other, "tag": "v3.0.0-alpha.3-rc.2"}
    (releases / other / "manifest.json").write_text(json.dumps(manifest))
    assert client.get("/v1/release").json()["release"] == RELEASE_ID
    (releases / "pointers" / "next").symlink_to(f"../{other}")
    (releases / "pointers" / "next").rename(releases / "pointers" / "k2-api")  # deploy's swap: one rename
    assert client.get("/v1/release").json() == {"release": other, "tag": "v3.0.0-alpha.3-rc.2",
                                                "commit": manifest["commit"], "builtAt": manifest["builtAt"]}


def test_no_pointer_or_one_out_of_the_releases_root_is_503(client: TestClient, releases: Path, tmp_path: Path) -> None:
    pointer = releases / "pointers" / "k2-api"
    pointer.unlink()
    r = client.get("/v1/release")
    assert r.status_code == 503 and set(r.json()) == {"error", "message"}
    (tmp_path / "elsewhere").mkdir()
    pointer.symlink_to(tmp_path / "elsewhere")
    assert client.get("/v1/release").status_code == 503


def test_a_manifest_naming_another_release_is_503(client: TestClient, releases: Path) -> None:
    path = releases / RELEASE_ID / "manifest.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"release": "v9.9.9-000000000000"}))
    assert client.get("/v1/release").status_code == 503


def test_the_served_document_is_the_frozen_file_and_says_3_1_0(client: TestClient) -> None:
    r = client.get("/v1/openapi.json")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    assert r.json() == yaml.safe_load(FROZEN.read_text())
    assert r.json()["openapi"] == "3.1.0"


def test_the_app_serves_exactly_the_frozen_routes_each_by_get() -> None:
    frozen = yaml.safe_load(FROZEN.read_text())["paths"]
    served = {route.path: route.methods for route in api.app.routes}
    assert set(served) == set(frozen)
    assert all(methods == {"GET"} for methods in served.values())
    assert all(set(ops) <= {"get", "parameters"} for ops in frozen.values())


def test_an_unknown_path_answers_the_frozen_error_body(client: TestClient) -> None:
    r = client.get("/v1/nothing")
    assert r.status_code == 404 and set(r.json()) == {"error", "message"}
    r = client.post("/v1/release")
    assert r.status_code == 405 and set(r.json()) == {"error", "message"}
