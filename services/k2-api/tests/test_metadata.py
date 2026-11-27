"""W3-2: /v1/products, one product's catalog document plus the caller's access, and its tables'
contracts, each resolved through the release's own index."""

import json
from pathlib import Path

import pytest
from conftest import DELAYED, FULL, RELEASE_ID, bearer
from fastapi.testclient import TestClient

STATION = "sensor-net.station-readings"


def doc(releases: Path, product: str = STATION) -> dict:
    return json.loads((releases / RELEASE_ID / f"compiled/catalog/products/{product}.json").read_text())


def test_products_is_the_index_of_the_release(client: TestClient, releases: Path, trino) -> None:
    index = json.loads((releases / RELEASE_ID / "compiled/catalog/index.json").read_text())
    r = client.get("/v1/products")
    assert (r.status_code, r.json()) == (200, index["products"])
    assert trino.calls == []


def test_anonymous_gets_the_catalog_document_and_no_access(client: TestClient, releases: Path, trino) -> None:
    r = client.get("/v1/products/sensor-net/station-readings")
    assert (r.status_code, r.json()) == (200, doc(releases))
    assert "access" not in r.json() and trino.calls == []


def test_a_key_adds_access_asked_of_trino_as_the_caller_with_limit_0(client: TestClient, releases: Path, trino) -> None:
    r = client.get("/v1/products/sensor-net/station-readings", headers=bearer(FULL))
    assert r.status_code == 200
    assert r.json() == doc(releases) | {"access": [
        {"relation": "lake.station_readings.bronze_readings", "allowed": False},  # a connector table offers no role
        {"relation": "sensor_net.station_readings.silver_readings", "allowed": True}]}
    assert trino.calls == [
        ("p.analyst-full", 'SELECT "station_id", "read_at", "value_text", "region", "operator_note", "source_file_sha256" '
                           'FROM "lake"."station_readings"."bronze_readings" LIMIT 0', []),
        ("p.analyst-full", 'SELECT "station_id", "read_at", "value", "region", "operator_note" '
                           'FROM "sensor_net"."station_readings"."silver_readings" LIMIT 0', [])]


def test_access_is_the_callers_own(client: TestClient, trino) -> None:
    r = client.get("/v1/products/ops-insight/daily-load", headers=bearer(DELAYED))
    assert r.json()["access"] == [{"relation": "ops_insight.daily_load.gold_daily_load", "allowed": False}]
    assert {user for user, _, _ in trino.calls} == {"p.analyst-delayed"}


def test_trino_down_while_asking_access_is_503(client: TestClient, trino) -> None:
    trino.down = True
    r = client.get("/v1/products/sensor-net/station-readings", headers=bearer(FULL))
    assert r.status_code == 503 and r.json()["error"] == "trino_unavailable"
    assert client.get("/v1/products/sensor-net/station-readings").status_code == 200  # anonymous needs no Trino


def test_the_contract_is_the_release_file_as_the_tag_approved_it(client: TestClient, releases: Path) -> None:
    r = client.get("/v1/products/sensor-net/station-readings/contracts/silver_readings.odcs.yaml")
    path = releases / RELEASE_ID / "documents/contracts/sensor-net/station-readings/silver_readings.odcs.yaml"
    assert r.status_code == 200 and r.content == path.read_bytes()
    assert r.headers["content-type"].startswith("application/yaml")


@pytest.mark.parametrize("path", [
    "/v1/products/sensor-net/nothing",
    "/v1/products/nothing/station-readings",
    "/v1/products/Sensor-net/station-readings",
    "/v1/products/sensor-net/station_readings",
    "/v1/products/sensor-net/station-readings/contracts/gold_daily_load.odcs.yaml",  # another product's table
    "/v1/products/sensor-net/station-readings/contracts/grants.odcs.yaml",  # a document, not a table
    "/v1/products/sensor-net/station-readings/contracts/product.odcs.yaml",
    "/v1/products/sensor-net/station-readings/contracts/Silver_readings.odcs.yaml",
])
def test_what_the_release_does_not_hold_is_404(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 404 and set(r.json()) == {"error", "message"}


@pytest.mark.parametrize("path", [
    "/v1/products/..%2F..%2F..%2Fetc/passwd",
    "/v1/products/sensor-net/..%2Fops-insight",
    "/v1/products/sensor-net/station-readings/contracts/..%2F..%2F..%2Fmanifest.odcs.yaml",
    "/v1/products/sensor-net/station-readings/contracts/%2E%2E.odcs.yaml",
    "/v1/products/sensor-net/station-readings/contracts/silver_readings%00.odcs.yaml",
    "/v1/products/%2e%2e/%2e%2e/contracts/manifest.odcs.yaml",
    "/v1/products/sensor-net/station-readings/contracts/....odcs.yaml",
])
def test_a_traversal_reaches_no_file(client: TestClient, releases: Path, path: str) -> None:
    (releases / "secret.odcs.yaml").write_text("not served")
    r = client.get(path)
    assert r.status_code == 404 and "not served" not in r.text


def test_a_contract_file_naming_a_path_out_of_the_release_is_refused(client: TestClient, releases: Path) -> None:
    path = releases / RELEASE_ID / f"compiled/catalog/products/{STATION}.json"
    product = json.loads(path.read_text())
    product["outputs"][1]["contractFile"] = "documents/contracts/../../../secret.odcs.yaml"
    path.write_text(json.dumps(product))
    (releases / "secret.odcs.yaml").write_text("not served")
    r = client.get("/v1/products/sensor-net/station-readings/contracts/silver_readings.odcs.yaml")
    assert r.status_code == 404 and "not served" not in r.text


def test_metadata_routes_need_no_key(client: TestClient) -> None:
    for path in ("/v1/products", "/v1/products/sensor-net/station-readings",
                 "/v1/products/sensor-net/station-readings/contracts/bronze_readings.odcs.yaml", "/v1/release",
                 "/v1/openapi.json", "/healthz"):
        assert client.get(path).status_code == 200, path
