"""The versions check (S06) held to its rules.

A fixture case is `fixtures/v2/bad/versions/<code>/{previous,next}/`: each side is the good set
(`fixtures/v2/good/`) with that side's files laid over it, path for path; an empty file removes
the document there, and a side with nothing to lay over has no directory. Each case yields its
code and nothing else. Every kind of change is also made in memory on the good set, so each
rule of the module has a test that fails when the rule breaks.
"""

import posixpath
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import model
from k2gen.checks import versions
from k2gen.model import DocumentSet, Finding, Provenance

FIXTURES = Path(__file__).parent / "fixtures" / "v2"
GOOD = FIXTURES / "good"
CASES = FIXTURES / "bad" / "versions"
STATION = "contracts/sensor-net/station-readings/product.odps.yaml"
DAILY = "contracts/ops-insight/daily-load/product.odps.yaml"
SILVER = "contracts/sensor-net/station-readings/silver_readings.odcs.yaml"
GOLD = "contracts/ops-insight/daily-load/gold_daily_load.odcs.yaml"
MAJOR, MINOR, PATCH = "versions.major-bump-missing", "versions.minor-bump-missing", "versions.version-reused"


def layered(*layers: Path) -> dict[str, bytes]:
    docs: dict[str, bytes] = {}
    for layer in layers:
        for path in sorted(layer.rglob("*")):
            if path.is_file():
                rel, data = path.relative_to(layer).as_posix(), path.read_bytes()
                if data:
                    docs[rel] = data
                else:
                    docs.pop(rel, None)
    return docs


def doc_set(documents: dict[str, bytes]) -> DocumentSet:
    return DocumentSet(documents, Provenance("git", "fixture", "0" * 40, "fixture", "2026-11-27T00:00:00Z",
                                             model.set_sha256(documents), ()))


def run(new: dict[str, bytes], old: dict[str, bytes] | None) -> list[Finding]:
    return versions.check(doc_set(new), doc_set(old) if old is not None else None)


def edited(path: str, change: Callable[[Any], object], docs: dict[str, bytes] | None = None) -> dict[str, bytes]:
    docs = dict(layered(GOOD) if docs is None else docs)
    doc = model.parse(path, docs[path])
    change(doc)
    docs[path] = yaml.safe_dump(doc, sort_keys=False).encode()
    return docs


def column(doc: Any, name: str) -> Any:
    return next(c for c in doc["schema"][0]["properties"] if c["name"] == name)


def policy(doc: Any, role: str = "delayed_reader") -> Any:
    return next(p["value"] for p in doc["customProperties"] if p["property"] == "policies")[role]


def lifecycle(**value: str) -> Callable[[Any], None]:
    return lambda d: d["customProperties"].append({"property": "lifecycleChange", "value": value})


def versioned(version: str) -> Callable[[Any], None]:
    return lambda d: d.update(version=version)


def without(docs: dict[str, bytes], product_file: str) -> dict[str, bytes]:
    home = posixpath.dirname(product_file) + "/"
    return {p: b for p, b in docs.items() if not p.startswith(home)}


@pytest.mark.parametrize("case", sorted(p.name for p in CASES.iterdir()))
def test_each_pair_yields_exactly_its_code(case: str) -> None:
    found = run(layered(GOOD, CASES / case / "next"), layered(GOOD, CASES / case / "previous"))
    assert {f.code for f in found} == {f"versions.{case}"}, found
    assert all(f.severity == "error" and f.message.startswith("/") for f in found), found


@pytest.mark.parametrize("side", sorted(f"{p.name}/{s}" for p in CASES.iterdir() for s in ("previous", "next")))
def test_each_side_is_a_document_set_the_model_loads(side: str) -> None:
    case, _, which = side.partition("/")
    model.load(doc_set(layered(GOOD, CASES / case / which)))  # raises on any schema error


def test_identical_sets_and_a_first_release_yield_nothing() -> None:
    good = layered(GOOD)
    assert run(good, good) == []
    assert run(good, None) == []


def add(required: bool) -> Callable[[Any], None]:
    new = {"name": "max_value", "physicalType": "decimal(12,3)", "required": required, "description": "The largest reading."}
    return lambda d: d["schema"][0]["properties"].append(new)


def swap_key(d: Any) -> None:
    column(d, "load_date")["primaryKeyPosition"], column(d, "station_id")["primaryKeyPosition"] = 2, 1


# (what, the table changed, the change, the code with both versions kept, what the message says)
CHANGES: list[tuple[str, str, Callable[[Any], object], str, str]] = [
    ("column dropped", GOLD, lambda d: d["schema"][0]["properties"].pop(), MAJOR, "column mean_value (decimal(12,3)) is gone"),
    ("column renamed", GOLD, lambda d: column(d, "readings").update(name="reading_count"), MAJOR, "column readings (bigint) is gone"),
    ("column retyped", GOLD, lambda d: column(d, "readings").update(physicalType="integer"), MAJOR, "retyped bigint -> integer"),
    ("required set", GOLD, lambda d: column(d, "mean_value").update(required=True), MAJOR, "mean_value required false -> true"),
    ("required unset", GOLD, lambda d: column(d, "readings").update(required=False), MAJOR, "readings required true -> false"),
    ("key shrunk", GOLD, lambda d: column(d, "station_id").update(primaryKey=False), MAJOR,
     "primary key (load_date, station_id) -> (load_date)"),
    ("key reordered", GOLD, swap_key, MAJOR, "primary key (load_date, station_id) -> (station_id, load_date)"),
    ("required column added", GOLD, add(required=True), MAJOR, "column max_value (decimal(12,3)) added as required"),
    ("nullable column added", GOLD, add(required=False), MINOR, "column max_value (decimal(12,3)) added nullable"),
    ("role removed", SILVER, lambda d: d["roles"].pop(), MAJOR, "role delayed_reader no longer offered"),
    ("role offered", GOLD, lambda d: d["roles"].append({"role": "public_reader", "access": "read"}), MINOR, "role public_reader offered"),
    ("delay lengthened", SILVER, lambda d: policy(d)["delay"].update(minutes=30), MAJOR, "tightened: delay read_at 20 min -> read_at 30 min"),
    ("delay moved", SILVER, lambda d: policy(d)["delay"].update(column="station_id"), MAJOR, "tightened: delay read_at 20 min -> station_id"),
    ("delay shortened", SILVER, lambda d: policy(d)["delay"].update(minutes=10), MINOR, "loosened: delay read_at 20 min -> read_at 10 min"),
    ("mask widened", SILVER, lambda d: policy(d)["mask"].append("region"), MAJOR, "tightened: mask adds region"),
    ("mask narrowed", SILVER, lambda d: policy(d)["mask"].clear(), MINOR, "loosened: mask drops operator_note"),
    ("row scope narrowed", SILVER, lambda d: policy(d)["rowScope"]["values"].pop(), MAJOR,
     "tightened: rowScope region in [north, south] -> region in [north]"),
    ("row scope widened", SILVER, lambda d: policy(d)["rowScope"]["values"].append("east"), MINOR,
     "loosened: rowScope region in [north, south] -> region in [north, south, east]"),
    ("row scope dropped", SILVER, lambda d: policy(d).pop("rowScope"), MINOR, "loosened: rowScope region in [north, south] -> every row"),
    ("row scope reordered", SILVER, lambda d: policy(d)["rowScope"]["values"].reverse(), PATCH, "the contract changed"),
    ("policy on an open role", GOLD, lambda d: d["customProperties"].append(
        {"property": "policies", "value": {"full_reader": {"mask": ["mean_value"]}}}), MAJOR, "role full_reader's policy tightened: mask adds mean_value"),
    ("description", GOLD, lambda d: d["description"].update(purpose="Reworded."), PATCH, "1.0.0 again, but a change: the contract changed"),
]


@pytest.mark.parametrize(("table", "change", "code", "says"), [c[1:] for c in CHANGES], ids=[c[0] for c in CHANGES])
def test_each_change_needs_its_bump_of_the_table_and_its_product(table: str, change: Callable[[Any], object], code: str, says: str) -> None:
    found = run(edited(table, change), layered(GOOD))
    product = posixpath.join(posixpath.dirname(table), model.V2_PRODUCT_FILE)
    assert sorted((f.code, f.path) for f in found) == sorted([(code, table), (code, product)]), found
    (on_table,) = [f for f in found if f.path == table]
    assert says in on_table.message


def dropped_with(table: str, product: str) -> dict[str, bytes]:
    docs = edited(GOLD, lambda d: (d["schema"][0]["properties"].pop(), d.update(version=table)))
    return edited(DAILY, versioned(product), docs)


@pytest.mark.parametrize(("table", "product", "expected"), [
    ("2.0.0", "2.0.0", []),
    ("3.1.4", "2.0.0", []),  # higher than needed is fine
    ("1.1.0", "2.0.0", [(MAJOR, GOLD)]),
    ("2.0.0", "1.9.0", [(MAJOR, DAILY)]),  # a table's major change is its product's too
    ("0.9.0", "2.0.0", [("versions.version-backwards", GOLD)]),
])
def test_a_major_change_passes_only_with_major_bumps(table: str, product: str, expected: list[tuple[str, str]]) -> None:
    assert [(f.code, f.path) for f in run(dropped_with(table, product), layered(GOOD))] == expected


def test_outputs_removed_added_and_other_files_bump_the_product() -> None:
    good = layered(GOOD)
    bronze = "contracts/sensor-net/station-readings/bronze_readings.odcs.yaml"
    removed = edited(STATION, lambda d: d["outputPorts"].pop(0))
    del removed[bronze]
    (found,) = run(removed, good)
    assert (found.code, found.path) == (MAJOR, STATION)
    assert "/outputPorts: output sensor-net.station-readings.bronze_readings 1.0.0 removed" in found.message

    extra = "contracts/ops-insight/daily-load/gold_extra.odcs.yaml"
    added = edited(DAILY, lambda d: d["outputPorts"].append(
        {"name": "gold_extra", "version": "1.0.0", "contractId": "ops-insight.daily-load.gold_extra"}))
    added[extra] = good[GOLD].replace(b"gold_daily_load", b"gold_extra")
    (found,) = run(added, good)
    assert (found.code, found.path) == (MINOR, DAILY)
    assert "/outputPorts/1: output ops-insight.daily-load.gold_extra added" in found.message
    assert run(edited(DAILY, versioned("1.1.0"), added), good) == []

    grants = "contracts/ops-insight/daily-load/grants.yaml"
    regranted = dict(good, **{grants: good[grants].replace(b"svc.load-dashboard", b"svc.other-dashboard")})
    (found,) = run(regranted, good)
    assert (found.code, found.path, found.message) == (
        PATCH, DAILY, "/version: 1.0.0 again, but a change: grants.yaml changed; make it 1.0.1 or higher")


STATES = ("active", "paused", "deprecated", "retired")
# ARCHITECTURE-V2 4.5, written out here so a change to the module's table fails this test.
DIAGRAM = {(s, s) for s in STATES} | {
    (None, "active"), (None, "paused"), ("active", "paused"), ("paused", "active"), ("active", "deprecated"),
    ("paused", "deprecated"), ("deprecated", "active"), ("deprecated", "retired"), ("retired", None)}


def daily_at(status: str | None, version: str) -> dict[str, bytes]:
    if status is None:
        return without(layered(GOOD), DAILY)
    return edited(DAILY, lambda d: d.update(status=status, version=version))


@pytest.mark.parametrize(("was", "now"), [(w, n) for w in (None, *STATES) for n in (*STATES, None) if (w, n) != (None, None)])
def test_transitions_follow_the_lifecycle_diagram(was: str | None, now: str | None) -> None:
    found = [f for f in run(daily_at(now, "1.0.1"), daily_at(was, "1.0.0")) if f.code == "versions.transition-not-allowed"]
    assert bool(found) == ((was, now) not in DIAGRAM), found
    assert all(f.path == DAILY for f in found)


@pytest.mark.parametrize(("since", "early"), [("2026-12-31", True), ("2027-01-01", False), ("2027-02-01", False)])
def test_retirement_waits_for_the_announced_sunset(since: str, early: bool) -> None:
    deprecated = edited(DAILY, lambda d: (d.update(status="deprecated"), lifecycle(
        since="2026-11-01", reason="r", decision="ADR-056", successor="ops-insight.next", sunset="2027-01-01")(d)))
    retired = edited(DAILY, lambda d: (d.update(status="retired", version="1.0.1"), lifecycle(
        since=since, reason="r", decision="ADR-056")(d)))
    found = run(retired, deprecated)
    assert [f.code for f in found] == (["versions.retired-before-sunset"] if early else [])
    if early:
        assert found[0].message == (f"/customProperties/2/value/since: retired since {since}, before the sunset 2027-01-01 "
                                    "the previous release announced; retire on or after 2027-01-01")


def test_only_a_live_reader_holds_a_retirement_back() -> None:
    retired = edited(STATION, lambda d: d.update(status="retired"))
    assert "versions.retired-while-input" in {f.code for f in run(retired, None)}
    both = edited(DAILY, lambda d: d.update(status="retired"), retired)
    assert "versions.retired-while-input" not in {f.code for f in run(both, None)}


def test_a_deprecated_product_keeps_its_readers_and_takes_no_new_one() -> None:
    deprecated = edited(STATION, lambda d: (d.update(status="deprecated"), lifecycle(
        since="2026-11-01", reason="r", decision="ADR-056", successor="sensor-net.next", sunset="2027-01-01")(d)))
    assert run(deprecated, deprecated) == []
    found = [f for f in run(deprecated, without(deprecated, DAILY)) if f.code == "versions.new-reader-of-deprecated"]
    assert [(f.path, f.message) for f in found] == [(DAILY, "/inputPorts/0: sensor-net.station-readings is deprecated (sunset "
                                                     "2027-01-01, successor sensor-net.next) and takes no new reader; read sensor-net.next instead")]


def test_a_reader_that_acknowledges_the_new_major_passes() -> None:
    case = CASES / "input-major-unacknowledged" / "next"
    (found,) = run(layered(GOOD, case), layered(GOOD))
    assert found.message == ("/inputPorts/0/version: input port silver_readings was written against "
                             "sensor-net.station-readings.silver_readings 1.0.0, but the set has it at 2.0.0, 1.0.0 in the "
                             "previous release; read what major 2 changed, then set this version to 2.0.0")
    acknowledged = edited(DAILY, lambda d: d.update(version="1.0.1", inputPorts=[{**d["inputPorts"][0], "version": "2.0.0"}]),
                          layered(GOOD, case))
    assert run(acknowledged, layered(GOOD)) == []


def test_a_paused_product_may_change_what_needs_no_build() -> None:
    paused = edited(STATION, lambda d: d.update(status="paused"))
    reworded = edited(SILVER, lambda d: d.update(version="1.0.1", description={"purpose": "Reworded."}), paused)
    assert run(edited(STATION, versioned("1.0.1"), reworded), paused) == []
