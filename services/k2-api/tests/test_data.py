"""W3-4: GET /v1/data/{domain}/{product}/{table}, read through the reader relation as the caller,
at the tested snapshot svc.api reads from the base's `$refs` before and after."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import DELAYED, FULL, KEYS, RELEASE_ID, bearer
from fastapi.testclient import TestClient

SILVER = "/v1/data/sensor-net/station-readings/silver_readings"
VIEW = "sensor_net.station_readings.silver_readings"
BASE = "sensor_net.station_readings__base.silver_readings"
REFS = ("svc.api", 'SELECT "snapshot_id" FROM "sensor_net"."station_readings__base"."silver_readings$refs" '
                   'WHERE "name" = ? AND "type" = ?', ["audited", "TAG"])
ALL = '"station_id", "read_at", "value", "region", "operator_note"'
BRONZE = "/v1/data/sensor-net/station-readings/bronze_readings"
GOLD = "/v1/data/ops-insight/daily-load/gold_daily_load"


def probe(user: str = "p.analyst-full") -> tuple:
    """The zero-row read as the caller that comes before anything else."""
    return (user, f'SELECT {ALL} FROM "sensor_net"."station_readings"."silver_readings" LIMIT 0', [])


SNAPSHOT = 5_000_000_000_000_000_001  # more than a JavaScript number holds exactly
ROWS = [
    {"station_id": "st-1", "read_at": datetime(2026, 1, 1, 0, 15, tzinfo=UTC), "value": Decimal("0E-3"),
     "region": "north", "operator_note": None},
    {"station_id": "st-2", "read_at": datetime(2026, 1, 1, 0, 30, 0, 123456, tzinfo=UTC), "value": Decimal("12.345"),
     "region": "south", "operator_note": "made up"},
    {"station_id": "st-3", "read_at": datetime(2026, 1, 1, 0, 45, tzinfo=UTC), "value": Decimal("-1.500"),
     "region": "north", "operator_note": None},
]


@pytest.fixture
def lake(trino):
    trino.rows[VIEW] = ROWS[::-1]  # stored out of key order: the page comes back sorted by the key
    trino.snapshots[BASE] = [SNAPSHOT]
    trino.snapshots["lake.station_readings.bronze_readings"] = [7]
    trino.snapshots["ops_insight.daily_load__base.gold_daily_load"] = [8]
    return trino


ORDER = ' ORDER BY "station_id", "read_at", "region"'  # the declared key, by primaryKeyPosition


def data(user: str, select: str, where: str = "", params: list | None = None, limit: int = 10001, order: str = ORDER) -> tuple:
    return (user, f'SELECT {select} FROM "sensor_net"."station_readings"."silver_readings"{where}{order} LIMIT {limit}', params or [])


def test_rows_of_the_view_as_the_caller_at_the_tested_snapshot(client: TestClient, lake) -> None:
    r = client.get(SILVER, headers=bearer(FULL))
    assert r.status_code == 200
    assert r.json() == {"release": RELEASE_ID, "snapshot": str(SNAPSHOT), "next": None, "rows": [
        {"station_id": "st-1", "read_at": "2026-01-01T00:15:00+00:00", "value": "0.000", "region": "north", "operator_note": None},
        {"station_id": "st-2", "read_at": "2026-01-01T00:30:00.123456+00:00", "value": "12.345", "region": "south",
         "operator_note": "made up"},
        {"station_id": "st-3", "read_at": "2026-01-01T00:45:00+00:00", "value": "-1.500", "region": "north", "operator_note": None}]}
    assert lake.calls == [probe(), REFS, data("p.analyst-full", ALL), REFS]  # a decimal is a string, never a float


def test_fields_filters_and_limit_shape_the_one_statement(client: TestClient, lake) -> None:
    r = client.get(SILVER, headers=bearer(DELAYED), params={
        "fields": "station_id,value", "filter.region": "north", "filter.value": "-1.5",
        "filter.read_at": "2026-01-01T10:45:00+10:00", "limit": "5"})
    assert r.status_code == 200 and r.json()["rows"][0] == {"station_id": "st-1", "value": "0.000"}
    assert lake.calls[2] == data("p.analyst-delayed", '"station_id", "value"',
                                 ' WHERE "region" = ? AND "value" = ? AND "read_at" = ?',
                                 ["north", Decimal("-1.5"), datetime(2026, 1, 1, 0, 45, tzinfo=UTC)], limit=6)


@pytest.mark.parametrize("value", ["north') OR (1=1", "x' OR '1'='1", "a\"; DROP TABLE x; --", "?", "' UNION SELECT 1 --"])
def test_a_value_is_bound_never_part_of_the_statement(client: TestClient, lake, value: str) -> None:
    r = client.get(SILVER, headers=bearer(FULL), params={"filter.region": value})
    assert r.status_code == 200
    assert lake.calls[2] == data("p.analyst-full", ALL, ' WHERE "region" = ?', [value])


@pytest.mark.parametrize("filter_, typed", [
    ({"filter.station_id": "st-1"}, "st-1"),
    ({"filter.value": "12.345"}, Decimal("12.345")),
    ({"filter.read_at": "2026-01-01T00:15:00Z"}, datetime(2026, 1, 1, 0, 15, tzinfo=UTC)),
])
def test_a_value_is_typed_by_its_declared_column(client: TestClient, lake, filter_: dict, typed) -> None:
    assert client.get(SILVER, headers=bearer(FULL), params=filter_).status_code == 200
    assert lake.calls[2][2] == [typed] and type(lake.calls[2][2][0]) is type(typed)


@pytest.mark.parametrize("params", [
    {"fields": "*"}, {"fields": ""}, {"fields": "station_id,"}, {"fields": "station_id,station_id"},
    {"fields": "nothing"}, {"fields": '"station_id"'}, {"fields": "station_id;drop"}, {"fields": "STATION_ID"},
    {"filter.nothing": "1"}, {'filter.region"': "x"}, {"filter.": "x"}, {"filter.region OR 1=1": "x"},
    {"filter.value": "abc"}, {"filter.value": "1e5"}, {"filter.value": "1.5.5"}, {"filter.read_at": "yesterday"},
    {"filter.read_at": "2026-01-01T00:15:00"},  # a timestamp with time zone needs its offset
    {"filter.region": "north\x00"},
    {"limit": "-1"}, {"limit": "0"}, {"limit": "10001"}, {"limit": "abc"}, {"limit": "5.0"}, {"limit": "+5"},
    {"limit": ""}, {"limit": "1e3"},
    {"sql": "SELECT 1"}, {"region": "north"}, {"Fields": "station_id"}, {"filter": "region"},
    {"cursor": "anything"},  # no page has a `next` until phase 007
])
def test_an_undeclared_name_or_a_malformed_value_is_400_with_no_query(client: TestClient, lake, params: dict) -> None:
    r = client.get(SILVER, headers=bearer(FULL), params=params)
    assert r.status_code == 400 and set(r.json()) == {"error", "message"}
    assert lake.calls == []
    assert all(v not in r.json()["message"] for v in params.values() if len(v) > 2)  # a value is never echoed


def test_a_parameter_given_twice_is_400(client: TestClient, lake) -> None:
    for query in ("limit=1&limit=2", "filter.region=north&filter.region=south", "fields=value&fields=region"):
        assert client.get(f"{SILVER}?{query}", headers=bearer(FULL)).status_code == 400
    assert lake.calls == []


def test_the_limit_is_up_to_10000(client: TestClient, lake) -> None:
    assert client.get(SILVER, headers=bearer(FULL), params={"limit": "10000"}).status_code == 200
    assert lake.calls[2] == data("p.analyst-full", ALL, limit=10001)


def test_more_rows_than_the_limit_is_422_result_too_large(client: TestClient, lake) -> None:
    r = client.get(SILVER, headers=bearer(FULL), params={"limit": "2"})
    assert r.status_code == 422 and r.json()["error"] == "result_too_large"
    assert client.get(SILVER, headers=bearer(FULL), params={"limit": "3"}).status_code == 200


def test_as_of_is_422_as_of_unavailable_whatever_its_value(client: TestClient, lake) -> None:
    for value in ("2026-01-01T00:00:00Z", "", "1"):
        r = client.get(SILVER, headers=bearer(FULL), params={"as_of": value})
        assert r.status_code == 422 and r.json()["error"] == "as_of_unavailable"
    assert lake.calls == []


def test_without_a_key_it_is_401_before_anything_else(client: TestClient, lake) -> None:
    for path in (SILVER, "/v1/data/nothing/nothing/nothing", f"{SILVER}?as_of=x"):
        assert client.get(path).status_code == 401
    assert lake.calls == []


@pytest.mark.parametrize("published", [True, False])
@pytest.mark.parametrize("untagged", [None, "INVALID_VIEW"])
@pytest.mark.parametrize("path, relation", [
    (BRONZE, '"lake"."station_readings"."bronze_readings"'),  # bronze offers no role: the plan's bronze_daily line
    (GOLD, '"ops_insight"."daily_load"."gold_daily_load"'),  # a view granted to another reader only
])
def test_a_caller_trino_denies_is_403_whatever_the_publication_state(
        client: TestClient, lake, path: str, relation: str, published: bool, untagged: str | None) -> None:
    if not published:
        lake.snapshots.clear()
    lake.untagged = untagged
    r = client.get(path, headers=bearer(FULL))
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    (call,) = lake.calls  # the caller's probe alone: svc.api never reads `$refs` for a caller Trino denies
    assert call[0] == "p.analyst-full" and call[1].endswith(f" FROM {relation} LIMIT 0") and call[2] == []


@pytest.mark.parametrize("path", [
    "/v1/data/sensor-net/station-readings/nothing", "/v1/data/sensor-net/nothing/silver_readings",
    "/v1/data/nothing/station-readings/silver_readings", "/v1/data/sensor-net/station-readings/Silver_readings",
    "/v1/data/sensor-net/station-readings/gold_daily_load", "/v1/data/sensor-net/station-readings/silver_readings$refs",
    "/v1/data/sensor-net/station-readings/station_readings__base.silver_readings",
    "/v1/data/sensor-net/station-readings/..%2F..%2Fops-insight%2Fdaily-load%2Fgold_daily_load",
])
def test_a_table_not_in_the_release_is_404(client: TestClient, lake, path: str) -> None:
    r = client.get(path, headers=bearer(FULL))
    assert r.status_code == 404 and set(r.json()) == {"error", "message"}
    assert lake.calls == []


def test_an_output_with_no_reader_relation_is_404(client: TestClient, releases: Path, lake) -> None:
    path = releases / RELEASE_ID / "compiled/catalog/products/sensor-net.station-readings.json"
    doc = json.loads(path.read_text())
    doc["outputs"][1] |= {"relation": None, "build": "serving", "testedRef": None}
    path.write_text(json.dumps(doc))
    assert client.get(SILVER, headers=bearer(FULL)).status_code == 404
    assert lake.calls == []


# None: the view reads zero rows; else the error the probe of a view over an untagged base is taken to raise.
@pytest.mark.parametrize("untagged", [None, "INVALID_VIEW", "INVALID_ARGUMENTS", "TABLE_NOT_FOUND"])
def test_a_table_never_published_is_404_not_published_to_a_caller_allowed_it(client: TestClient, lake, untagged) -> None:
    lake.snapshots[BASE] = []
    lake.untagged = untagged
    r = client.get(SILVER, headers=bearer(FULL))
    assert r.status_code == 404 and r.json()["error"] == "not_published"
    assert lake.calls == [probe(), REFS]  # the caller first, then svc.api; the caller's read never ran


def test_a_probe_failing_for_another_reason_is_503_with_no_refs_read(client: TestClient, lake) -> None:
    lake.snapshots[BASE] = []
    lake.untagged = "COLUMN_NOT_FOUND"
    r = client.get(SILVER, headers=bearer(FULL))
    assert r.status_code == 503 and r.json()["error"] == "query_failed"
    assert lake.calls == [probe()]


def test_a_tag_moved_during_the_read_reads_once_more(client: TestClient, lake) -> None:
    lake.snapshots[BASE] = [1, 2, 2]
    r = client.get(SILVER, headers=bearer(FULL))
    assert r.status_code == 200 and r.json()["snapshot"] == "2"
    assert lake.calls == [probe(), REFS, data("p.analyst-full", ALL), REFS, data("p.analyst-full", ALL), REFS]


def test_a_tag_that_keeps_moving_is_503(client: TestClient, lake) -> None:
    lake.snapshots[BASE] = [1, 2, 3, 3]
    r = client.get(SILVER, headers=bearer(FULL))
    assert r.status_code == 503 and r.json()["error"] == "snapshot_moving"


def test_trino_down_is_503(client: TestClient, lake) -> None:
    lake.down = True
    r = client.get(SILVER, headers=bearer(FULL))
    assert r.status_code == 503 and r.json()["error"] == "trino_unavailable"


def test_the_query_runs_as_the_keys_principal_and_never_another(client: TestClient, releases: Path, lake) -> None:
    callers = json.loads((releases / RELEASE_ID / "compiled/api/principals.json").read_text())["principals"]
    for key in (FULL, DELAYED):
        client.get(SILVER, headers=bearer(key))
    users = [user for user, sql, _ in lake.calls if "$refs" not in sql]
    assert users == ["p.analyst-full"] * 2 + ["p.analyst-delayed"] * 2  # each caller's probe, then its read
    assert {user for user, sql, _ in lake.calls if "$refs" in sql} == {"svc.api"}
    assert set(users) <= set(callers) and set(KEYS.values()) - set(callers) == {"p.not-served", "svc.api"}


def test_the_release_lets_svc_api_become_exactly_its_principals(releases: Path, lake) -> None:
    callers = json.loads((releases / RELEASE_ID / "compiled/api/principals.json").read_text())["principals"]
    for user in [*callers, "p.not-served", "svc.api", "svc.platform", "pub_station_readings", "dbt_station_readings"]:
        rule = [r for r in lake.rules["impersonation"] if re.fullmatch(r["new_user"], user)]
        assert bool(rule) == (user in callers), user


def test_rows_are_sorted_by_the_declared_key_in_its_positions(client: TestClient, releases: Path, lake) -> None:
    path = releases / RELEASE_ID / "compiled/catalog/products/sensor-net.station-readings.json"
    doc = json.loads(path.read_text())
    columns = doc["outputs"][1]["columns"]  # silver: station_id 1, read_at 2, region 3
    positions = {"region": 1, "station_id": 2}
    for c in columns:
        c["primaryKey"] = c["name"] in (*positions, "value")  # value: a key column with no position sorts last
        c.pop("primaryKeyPosition", None)
        if c["name"] in positions:
            c["primaryKeyPosition"] = positions[c["name"]]
    path.write_text(json.dumps(doc))
    r = client.get(SILVER, headers=bearer(FULL), params={"fields": "station_id"})
    assert lake.calls[2] == data("p.analyst-full", '"station_id"', order=' ORDER BY "region", "station_id", "value"')
    assert [row["station_id"] for row in r.json()["rows"]] == ["st-1", "st-3", "st-2"]  # north before south


def test_a_table_with_no_declared_key_has_no_order_by(client: TestClient, releases: Path, lake) -> None:
    path = releases / RELEASE_ID / "compiled/catalog/products/sensor-net.station-readings.json"
    doc = json.loads(path.read_text())
    for c in doc["outputs"][1]["columns"]:
        c["primaryKey"] = False
        c.pop("primaryKeyPosition", None)
    path.write_text(json.dumps(doc))
    assert client.get(SILVER, headers=bearer(FULL)).status_code == 200
    assert lake.calls[2] == data("p.analyst-full", ALL, order="")
