import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("k2_kraken_asset_pairs", HERE.parent / "parsers" / "kraken_asset_pairs.py")
kap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kap)

FIXTURE = json.loads((HERE / "fixtures" / "asset-pairs-synthetic.json").read_text())
BRONZE = HERE.parents[2] / "contracts" / "market-capture" / "kraken-instruments" / "bronze_pairs.odcs.yaml"
SET_BY_CONNECTOR = {"source_file_sha256", "fetched_at"}


def raw(doc) -> bytes:
    return json.dumps(doc).encode()


def changed(**pair_changes) -> dict:
    """The fixture with ALPHAUSD's attributes changed (a value of None removes the attribute)."""
    doc = copy.deepcopy(FIXTURE)
    for k, v in pair_changes.items():
        if v is None:
            del doc["result"]["ALPHAUSD"][k]
        else:
            doc["result"]["ALPHAUSD"][k] = v
    return doc


def test_fixture_gives_one_row_per_pair_sorted_strings_as_published():
    rows = kap.parse(raw(FIXTURE))
    assert [r["pair_code"] for r in rows] == ["ALPHAUSD", "CHARLIEUSDT", "XBRAVZEUR"]
    assert rows[0] == {"pair_code": "ALPHAUSD", "ws_name": "ALPHA/USD", "base": "ALPHA", "quote": "ZUSD",
                       "tick_size": "0.1", "pair_decimals": 1, "lot_decimals": 8, "order_min": "0.0001",
                       "status": "online"}
    by = {r["pair_code"]: r for r in rows}
    assert (by["XBRAVZEUR"]["base"], by["XBRAVZEUR"]["quote"]) == ("XBRAV", "ZEUR")  # as the venue spells them
    assert by["XBRAVZEUR"]["status"] == "cancel_only"


def test_rows_are_the_bronze_columns_but_those_the_connector_sets():
    contract = yaml.safe_load(BRONZE.read_text())
    columns = {c["name"] for c in contract["schema"][0]["properties"]}
    assert SET_BY_CONNECTOR <= columns
    assert all(set(r) == columns - SET_BY_CONNECTOR for r in kap.parse(raw(FIXTURE)))


def test_a_change_in_an_attribute_not_kept_leaves_the_rows_as_they_were():
    assert kap.parse(raw(changed(fees=[[0, 0.3]], long_position_limit=200))) == kap.parse(raw(FIXTURE))


def test_an_error_array_that_is_not_empty_fails_the_fetch():
    assert kap.parse(raw({"error": [], "result": FIXTURE["result"]}))
    with pytest.raises(ValueError, match="errors"):
        kap.parse(raw({"error": ["EGeneral:Temporary lockout"], "result": FIXTURE["result"]}))
    with pytest.raises(ValueError, match="errors"):
        kap.parse(raw({"error": ["EService:Unavailable"]}))  # an error answer may carry no result


def test_zero_pairs_fails_the_fetch():
    with pytest.raises(ValueError, match="no pairs"):
        kap.parse(raw({"error": [], "result": {}}))


@pytest.mark.parametrize("field", ["base", "quote"])
@pytest.mark.parametrize("value", [None, "", " ", 7])
def test_a_pair_missing_base_or_quote_fails_the_fetch(field, value):
    with pytest.raises(ValueError, match="no base or no quote"):
        kap.parse(raw(changed(**{field: value})))


def test_duplicate_websocket_names_fail_the_fetch():
    with pytest.raises(ValueError, match="BRAV/EUR"):
        kap.parse(raw(changed(wsname="BRAV/EUR")))


def test_a_repeated_key_fails_the_fetch_instead_of_keeping_the_last():
    one = json.dumps(FIXTURE["result"]["ALPHAUSD"])
    with pytest.raises(ValueError, match="twice"):
        kap.parse(f'{{"error": [], "result": {{"ALPHAUSD": {one}, "ALPHAUSD": {one}}}}}'.encode())


@pytest.mark.parametrize("doc", [
    changed(wsname=None),
    changed(tick_size=0.1),  # a number, not the published string
    changed(tick_size="1e-5"),
    changed(ordermin="0.0000000000000000001"),  # 19 decimals: more than silver's decimal(38,18) holds
    changed(pair_decimals=True),
    changed(lot_decimals=-1),
    changed(status=None),
    {"error": [], "result": {"ALPHAUSD": []}},
    {"result": FIXTURE["result"]},
    [],
])
def test_a_malformed_response_fails_the_fetch(doc):
    with pytest.raises(ValueError):
        kap.parse(raw(doc))


@pytest.mark.parametrize("data", [b"", b"{", b'{"error": [], "result": NaN}', raw(FIXTURE).decode().encode("utf-16")])
def test_bytes_that_are_not_utf8_json_fail_the_fetch(data):
    with pytest.raises(ValueError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        kap.parse(data)
