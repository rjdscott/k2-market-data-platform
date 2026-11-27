"""model.load (S04): the good fixture set into the model, and each way a set is refused.

Every refusal is one line `<path>: <why>`, a schema error's path with the JSON pointer into
the document. Each bad case is the good set with one change, made here in memory.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import model
from k2gen.model import DocumentError, DocumentSet

GOOD = Path(__file__).parent / "fixtures" / "v2" / "good"
STATION = "contracts/sensor-net/station-readings"
PRODUCT = f"{STATION}/product.odps.yaml"
SILVER = f"{STATION}/silver_readings.odcs.yaml"
BRONZE = f"{STATION}/bronze_readings.odcs.yaml"


def good() -> dict[str, bytes]:
    return {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in sorted(GOOD.rglob("*")) if p.is_file()}


def docset(documents: dict[str, bytes]) -> DocumentSet:
    provenance = model.Provenance("git", "v0.1.0", "0" * 40, "Example Owner <owner@example.org>",
                                  "2026-01-01T00:00:00Z", model.set_sha256(documents), ())
    return DocumentSet(documents, provenance)


def edited(path: str, change: Callable[[Any], None]) -> dict[str, bytes]:
    """The good set with one document changed through its parsed form."""
    documents = good()
    doc = yaml.safe_load(documents[path])
    change(doc)
    documents[path] = yaml.safe_dump(doc, sort_keys=False).encode()
    return documents


def refused(documents: dict[str, bytes]) -> list[str]:
    with pytest.raises(DocumentError) as e:
        model.load(docset(documents))
    return str(e.value).splitlines()


def test_the_good_set_loads_into_the_model() -> None:
    docs = docset(good())
    m = model.load(docs, {"trino": "trinodb/trino@sha256:" + "f" * 64})
    assert [(d.name, d.owner, d.kind) for d in m.domains] == [("sensor-net", "sensor-team", "source-aligned"),
                                                               ("ops-insight", "insight-team", "consumer-aligned")]
    assert [r.name for r in m.roles] == ["full_reader", "delayed_reader", "public_reader"]
    assert m.roles[1].requires == ("delay",) and m.roles[0].requires == ()
    assert len(m.principals) == 11 and m.principals[1] == model.Principal("p.analyst-delayed", "person", "insight-team", None)
    assert m.provenance == docs.provenance and m.images == {"trino": "trinodb/trino@sha256:" + "f" * 64}
    assert model.load(docs).images == {}

    load, station = m.products  # sorted by directory: ops-insight before sensor-net
    assert (station.path, station.id, station.domain, station.owner, station.type) == (
        STATION, "sensor-net.station-readings", "sensor-net", "sensor-team", "sourceAligned")
    assert station.licence == "https://data.example.org/terms" and station.limitations and station.usage is None
    assert station.connector == model.Connector("https-file", {"url": "https://data.example.org/readings/{{ds}}.csv",
                                                               "allowHosts": ["data.example.org"], "format": "csv", "parser": "station_csv"})
    assert station.schedule == model.Schedule("15 * * * *", 10) and load.schedule == model.Schedule("30 1 * * *", None)
    assert station.terms == model.Terms("allowed", "Source: a synthetic feed") and load.terms == model.Terms("forbidden", None)
    assert station.examples[0].sql.startswith("SELECT station_id") and station.lifecycle_change is None
    assert [p.contract_id for p in station.output_ports] == ["sensor-net.station-readings.bronze_readings", "sensor-net.station-readings.silver_readings"]
    assert station.grants == (model.Grant("p.analyst-full", "full_reader"), model.Grant("p.analyst-delayed", "delayed_reader"))
    assert list(station.models) == ["silver_readings.sql"] and "incremental" in station.models["silver_readings.sql"]
    assert station.unit_tests == (GOOD / STATION / "unit_tests.yml").read_text() and load.unit_tests is None
    assert station.seeds == {} and load.connector is None

    (port,) = load.input_ports
    assert port == model.InputPort("silver_readings", "1.0.0", "sensor-net.station-readings.silver_readings", True, 120)
    assert load.output_ports[0].input_contracts == (model.ContractRef("sensor-net.station-readings.silver_readings", "1.0.0"),)
    assert load.output_ports[0].serves is None

    bronze, silver = station.tables  # sorted by path
    assert (bronze.path, bronze.build, bronze.layer, bronze.retention, bronze.partitioning) == (BRONZE, model.Build("connector", None), "bronze", 7, ())
    assert {c.name: c.pattern for c in bronze.columns if c.pattern} == {"station_id": "^s-[0-9]+$"}
    assert bronze.quality == (model.Quality("value_parses", "custom", "k2-connector", "Every value_text parses as a decimal.", "value_parses"),)
    assert (silver.physical_name, silver.build, silver.partitioning, silver.sort) == (
        "station_readings.silver_readings", model.Build("dbt", None), ("day(read_at)", "region"), ("station_id",))
    assert silver.policies == {"delayed_reader": model.RolePolicy(model.Delay("read_at", 20), ("operator_note",),
                                                                  model.RowScope("region", ("north", "south")))}
    assert silver.roles == (model.TableRole("full_reader", None), model.TableRole("delayed_reader", "sensor-team"))
    assert silver.sla == (model.Sla("latency", 90, "min", "silver_readings.read_at"),)
    read_at, value = silver.columns[1], silver.columns[2]
    assert (read_at.primary_key, read_at.primary_key_position, read_at.required, read_at.time) == (True, 2, True, "event")
    assert (value.physical_type, value.unit, value.primary_key, value.time) == ("decimal(12,3)", "degree Celsius", False, None)
    gold = load.tables[0]
    assert gold.retention is None and gold.policies == {}
    assert gold.columns[1].relationships == (model.Relationship(
        "sensor-net/station-readings/silver_readings.odcs.yaml#/schema/silver_readings/properties/station_id", None),)


def test_the_expectations_load_as_their_five_shapes() -> None:
    rows = model.load(docset(good())).expectations
    shapes = [type(r).__name__ for r in rows]
    assert {s: shapes.count(s) for s in set(shapes)} == {"TableExpectation": 3, "DeniedExpectation": 5, "StatementExpectation": 10,
                                                         "ProbeExpectation": 2, "DriftExpectation": 1}
    assert rows[1] == model.TableExpectation("p.analyst-delayed", "sensor_net.station_readings.silver_readings", ("sql",),
                                             ("station_id", "read_at", "value", "region", "operator_note"), ("operator_note",), 20,
                                             "region NOT IN ('north', 'south')", model.MustAppear(1, "region IN ('north', 'south')"))
    assert rows[0].must_appear == model.MustAppear(1, None)  # type: ignore[union-attr]
    assert rows[3] == model.DeniedExpectation("p.analyst-full", "sensor_net.station_readings__base.silver_readings", ("sql",))
    assert rows[11] == model.ProbeExpectation("k2-net", "lakekeeper:8181", "refused") and rows[13] == model.DriftExpectation("catalog", "clean")
    assert rows[-1].session == {"sensor_net.statistics_enabled": "true"} and rows[7].session == {}  # type: ignore[union-attr]


def test_a_set_without_the_optional_documents_loads_empty() -> None:
    m = model.load(docset({model.DOMAINS: good()[model.DOMAINS], model.COMPOSE: b"services: {}\n"}))
    assert len(m.domains) == 2 and (m.products, m.roles, m.principals, m.expectations) == ((), (), (), ())


def test_an_input_port_is_required_unless_it_says_otherwise() -> None:
    documents = edited("contracts/ops-insight/daily-load/product.odps.yaml", lambda d: d["inputPorts"][0].pop("customProperties"))
    (port,) = model.load(docset(documents)).products[0].input_ports
    assert (port.required, port.max_lag_minutes) == (True, None)


def test_a_declared_seed_loads_under_its_name() -> None:
    documents = edited(PRODUCT, lambda d: d["customProperties"].append(
        {"property": "seeds", "value": [{"path": "seeds/station_regions.csv", "synthetic": True}]}))
    documents[f"{STATION}/seeds/station_regions.csv"] = b"station_id,region\ns-1,north\n"
    (station,) = [p for p in model.load(docset(documents)).products if p.name == "station-readings"]
    assert station.seeds == {"station_regions.csv": b"station_id,region\ns-1,north\n"}


def _set(*keys: Any, value: Any) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        node = doc
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value

    return change


def _append(*keys: Any, value: Any) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        node = doc
        for key in keys:
            node = node[key]
        node.append(value)

    return change


def _with(path: str, data: bytes) -> dict[str, bytes]:
    return {**good(), path: data}


def _seed_declared() -> dict[str, bytes]:
    return edited(PRODUCT, _append("customProperties", value={"property": "seeds", "value": [{"path": "seeds/regions.csv", "synthetic": True}]}))


BAD: dict[str, tuple[Callable[[], dict[str, bytes]], str]] = {
    "a duplicate YAML key": (
        lambda: _with(SILVER, good()[SILVER].replace(b"        mask: [operator_note]\n", b"        mask: [operator_note]\n        mask: []\n")),
        f"{SILVER}: line 96: key 'mask' appears twice"),
    "YAML that does not parse": (lambda: _with(PRODUCT, b"apiVersion: [v1.1.0\n"), f"{PRODUCT}: while parsing a flow sequence"),
    "a reserved word as a column, by the profile": (
        lambda: edited(SILVER, _set("schema", 0, "properties", 3, "name", value="current_user")),
        f"{SILVER}#/schema/0/properties/3/name: k2-v2.schema.json: 'current_user' should not be valid under"),
    "an unknown product field, by the published standard": (
        lambda: edited(PRODUCT, _set("owner", value="sensor-team")),
        f"{PRODUCT}#: odps-json-schema-v1.1.0.json: Additional properties are not allowed ('owner' was unexpected)"),
    "a bad grant": (
        lambda: edited(f"{STATION}/grants.yaml", _set("grants", 0, "expires", value="2027-01-01")),
        f"{STATION}/grants.yaml#/grants/0: k2-v2.schema.json: Additional properties are not allowed ('expires' was unexpected)"),
    "a hash in principals.yaml": (
        lambda: edited(model.PRINCIPALS, _set("principals", 0, "trinoBcrypt", value="x")),
        f"{model.PRINCIPALS}#/principals/0: k2-v2.schema.json: Additional properties are not allowed ('trinoBcrypt' was unexpected)"),
    "an unknown role requirement": (
        lambda: edited(model.ROLES, _set("roles", "delayed_reader", "requires", value=["filter"])),
        f"{model.ROLES}#/roles/delayed_reader/requires/0: k2-v2.schema.json: 'filter' is not one of"),
    "a denial that is not denied": (
        lambda: edited(model.EXPECTATIONS, _set("expectations", 3, "denied", value=False)),
        f"{model.EXPECTATIONS}#/expectations/3/denied: expectations.schema.json: True was expected"),
    "a property declared twice": (
        lambda: edited(SILVER, _append("customProperties", value={"property": "retention", "value": 30})),
        f"{SILVER}#/customProperties/7: custom property retention appears twice"),
    "a column property declared twice": (
        lambda: edited(SILVER, _append("schema", 0, "properties", 2, "customProperties", value={"property": "unit", "value": "kelvin"})),
        f"{SILVER}#/schema/0/properties/2/customProperties/1: custom property unit appears twice"),
    "quality on a column": (
        lambda: edited(SILVER, _set("schema", 0, "properties", 0, "quality", value=[{"type": "text", "description": "x"}])),
        f"{SILVER}#/schema/0/properties/0/quality: K2 reads quality on the table"),
    "a contract beside no product file": (
        lambda: _with("contracts/sensor-net/other/silver_readings.odcs.yaml", good()[SILVER]),
        "contracts/sensor-net/other/silver_readings.odcs.yaml: contracts/sensor-net/other/ holds no product.odps.yaml"),
    "a model beside no product file": (
        lambda: _with("contracts/sensor-net/other/models/x.sql", b"select 1"),
        "contracts/sensor-net/other/models/x.sql: contracts/sensor-net/other/ holds no product.odps.yaml"),
    "a seed nobody declared": (lambda: _with(f"{STATION}/seeds/regions.csv", b"a\n1\n"),
                               f"{STATION}/seeds/regions.csv: not declared by the product's seeds property"),
    "a declared seed the set lacks": (_seed_declared, f"{PRODUCT}#/customProperties: declares seed seeds/regions.csv, which the set does not hold"),
    "two licences": (
        lambda: edited(PRODUCT, _append("authoritativeDefinitions", value={"type": "licence", "url": "https://data.example.org/other"})),
        f"{PRODUCT}#/authoritativeDefinitions: 2 entries of type licence"),
    "a unit test file with a duplicate key": (
        lambda: _with(f"{STATION}/unit_tests.yml", b"unit_tests: []\nunit_tests: []\n"),
        f"{STATION}/unit_tests.yml: line 2: key 'unit_tests' appears twice"),
    "a model that is not UTF-8": (lambda: _with(f"{STATION}/models/silver_readings.sql", b"select '\xff'"),
                                  f"{STATION}/models/silver_readings.sql: not UTF-8 text"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_each_bad_set_is_refused_with_its_finding(case: str) -> None:
    make, finding = BAD[case]
    lines = refused(make())
    assert any(line.startswith(finding) for line in lines), lines


def test_every_finding_is_reported_at_once_standard_before_profile() -> None:
    documents = edited(PRODUCT, _set("status", value=7))
    documents[SILVER] = yaml.safe_dump({**yaml.safe_load(good()[SILVER]), "name": "x__base"}).encode()
    lines = refused(documents)
    status = [line for line in lines if line.startswith(f"{PRODUCT}#/status: ")]
    assert [line.split(": ")[1] for line in status] == ["odps-json-schema-v1.1.0.json", "k2-v2.schema.json"], lines
    assert any(line.startswith(f"{SILVER}#/name: k2-v2.schema.json: ") for line in lines), lines


def test_the_pointer_escapes_slash_and_tilde() -> None:
    assert model._pointer(["a/b", "c~d", 0]) == "/a~1b/c~0d/0"
