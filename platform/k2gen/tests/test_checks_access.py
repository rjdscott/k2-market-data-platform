"""k2gen.checks.access (step S07): the good set is clean, and each failing fixture of
tests/fixtures/v2/bad/access/cases.yaml yields exactly its code."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import model
from k2gen.checks import access

FIXTURES = Path(__file__).parent / "fixtures" / "v2"
GOOD = FIXTURES / "good"
CASES = model.parse("cases.yaml", (FIXTURES / "bad" / "access" / "cases.yaml").read_bytes())["cases"]
CODES = {"principal-duplicate", "principal-collides", "role-undeclared", "policy-role-not-offered", "policy-column-unknown",
         "role-requirement-unmet", "grant-unknown-principal", "grant-unknown-role", "grant-role-not-offered", "grant-two-roles",
         "grant-to-platform", "inherit-role", "inherit-delay", "inherit-mask", "inherit-row-scope", "inherit-undeclared",
         "expectation-missing", "expectation-weak", "expectation-unknown-principal"}
SILVER = "contracts/sensor-net/station-readings/silver_readings.odcs.yaml"
GOLD = "contracts/ops-insight/daily-load/gold_daily_load.odcs.yaml"


def good() -> dict[str, bytes]:
    return {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in GOOD.rglob("*") if p.is_file()}


def docset(documents: dict[str, bytes]) -> model.DocumentSet:
    provenance = model.Provenance("git", "fixture", "0" * 40, "fixture", "2026-01-01T00:00:00Z", model.set_sha256(documents), ())
    return model.DocumentSet(documents, provenance)


def change(doc: Any, step: dict[str, Any]) -> None:
    op = next(k for k in ("set", "append", "remove") if k in step)
    *parents, last = step[op].split("/")
    node = doc
    for key in parents:
        node = node[int(key) if isinstance(node, list) else key]
    key = int(last) if isinstance(node, list) else last
    if op == "set":
        node[key] = copy.deepcopy(step["value"])
    elif op == "append":
        node[key].append(copy.deepcopy(step["value"]))
    else:
        del node[key]


def changed(steps: list[dict[str, Any]]) -> dict[str, bytes]:
    documents = good()
    parsed: dict[str, Any] = {}
    for step in steps:
        doc = parsed.setdefault(step["file"], model.parse(step["file"], documents[step["file"]]))
        change(doc, step)
    for path, doc in parsed.items():
        assert model.schema_errors(model.document_kind(path), doc) == [], f"{path} no longer fits its schema"
        documents[path] = yaml.safe_dump(doc, sort_keys=False).encode()
    return documents


def codes(documents: dict[str, bytes]) -> set[str]:
    return {f.code for f in access.check(docset(documents), None)}


def test_the_good_set_is_clean() -> None:
    assert access.check(docset(good()), None) == []


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_each_failing_fixture_yields_exactly_its_code(case: dict[str, Any]) -> None:
    found = access.check(docset(changed(case["changes"])), None)
    assert {f.code for f in found} == {case["code"]}, [f.message for f in found]
    assert all(f.severity == "error" and f.message for f in found)


def test_every_code_has_a_failing_fixture() -> None:
    assert {c["code"] for c in CASES} == {f"access.{c}" for c in CODES}


def _derived(column: int, *sources: str) -> dict[str, Any]:
    return {"file": GOLD, "set": f"schema/0/properties/{column}/transformSourceObjects",
            "value": [f"sensor-net.station-readings.silver_readings.{s}" for s in sources]}


def test_an_output_at_least_as_strict_on_every_dimension_passes() -> None:
    # delay 30 >= 20 on a column derived from read_at; scope [north] within [north, south] on a
    # column derived from region; operator_note reaches no column of the output.
    steps = [
        {"file": SILVER, "set": "customProperties/6/value/delayed_reader",
         "value": {"delay": {"column": "read_at", "minutes": 20}, "mask": ["operator_note"],
                   "rowScope": {"column": "region", "values": ["north", "south"]}}},
        {"file": GOLD, "append": "roles", "value": {"role": "delayed_reader", "access": "read"}},
        {"file": GOLD, "append": "customProperties", "value": {"property": "policies", "value": {"delayed_reader": {
            "delay": {"column": "load_date", "minutes": 30}, "rowScope": {"column": "station_id", "values": ["north"]}}}}},
        _derived(0, "read_at"), _derived(1, "station_id", "region"), _derived(2, "station_id"), _derived(3, "value"),
    ]
    assert codes(changed(steps)) == set()


def test_a_masked_input_column_may_reach_the_output_masked() -> None:
    steps = [
        {"file": SILVER, "set": "customProperties/6/value/delayed_reader",
         "value": {"delay": {"column": "read_at", "minutes": 20}, "mask": ["operator_note"]}},
        {"file": GOLD, "append": "roles", "value": {"role": "delayed_reader", "access": "read"}},
        {"file": GOLD, "append": "customProperties", "value": {"property": "policies", "value": {"delayed_reader": {
            "delay": {"column": "load_date", "minutes": 20}, "mask": ["readings"]}}}},
        _derived(0, "read_at"), _derived(1, "station_id"), _derived(2, "operator_note"), _derived(3, "value"),
    ]
    assert codes(changed(steps)) == set()


def test_inputs_are_every_input_port_when_the_output_port_lists_none() -> None:
    # without inputContracts the gold table still derives from silver, through the input port
    steps = [{"file": "contracts/ops-insight/daily-load/product.odps.yaml", "remove": "outputPorts/0/inputContracts"},
             {"file": GOLD, "append": "roles", "value": {"role": "public_reader", "access": "read"}}]
    assert codes(changed(steps)) == {"access.inherit-role"}


def test_a_grant_to_each_platform_principal_is_refused() -> None:
    for principal in ("dbt_station_readings", "pub_station_readings", "svc.platform", "svc.catalog", "svc.api"):
        steps = [{"file": "contracts/ops-insight/daily-load/grants.yaml", "append": "grants",
                  "value": {"principal": principal, "role": "full_reader"}}]
        assert codes(changed(steps)) == {"access.grant-to-platform"}, principal


def test_a_missing_row_names_what_to_add() -> None:
    found = access.check(docset(changed([{"file": "tests/access/expectations.yaml", "remove": "expectations/1"}])), None)
    assert len(found) == 1 and found[0].path == model.EXPECTATIONS
    assert "sensor_net.station_readings.silver_readings" in found[0].message and "delayed_reader" in found[0].message


def test_a_sibling_in_input_contracts_is_followed_to_what_it_derives_from() -> None:
    # gold lists its sibling silver, which derives from the input X: gold inherits X's policy
    def port(cid: str, *inputs: str) -> dict[str, Any]:
        return {"contractId": cid, "inputContracts": [{"id": i, "version": "1.0.0"} for i in inputs]}

    doc = {"inputPorts": [{"contractId": "d.other.x"}], "outputPorts": [port("d.p.silver", "d.other.x"), port("d.p.gold", "d.p.silver")]}
    product = access._Product("contracts/d/p", "d", "p", doc, {"s": {"id": "d.p.silver"}, "g": {"id": "d.p.gold"}})
    assert access._inputs(product, {"id": "d.p.gold"}) == ["d.other.x"]
    doc["outputPorts"][0] = port("d.p.silver", "d.p.gold")  # a cycle of siblings ends, deriving from nothing
    assert access._inputs(product, {"id": "d.p.gold"}) == []
