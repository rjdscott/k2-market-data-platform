"""emit/catalog.py (S18): the catalog JSON of the good fixture set and of the repository's products.

Goldens: `golden/catalog/<release path>` for the good set; `golden/catalog/repository/<release
path>` for each real product under contracts/, compared while that product exists (none does
until S10's canary and hot-gold merge). Regenerate both, then review the diff as code:

    cd platform/k2gen && uv run --locked python tests/test_emit_catalog.py [<repository root>]
"""

import dataclasses
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import model
from k2gen.emit import catalog
from k2gen.model import DocumentSet, Model

GOOD = Path(__file__).parent / "fixtures" / "v2" / "good"
GOLDEN = Path(__file__).parent / "golden" / "catalog"
REPOSITORY = GOLDEN / "repository"
TAG = "v3.0.0-alpha.3-rc.1"
DAILY = "contracts/ops-insight/daily-load"
STATION = "contracts/sensor-net/station-readings"


def good() -> dict[str, bytes]:
    return {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in sorted(GOOD.rglob("*")) if p.is_file()}


def repository(root: Path = model.REPO) -> dict[str, bytes]:
    """The files under model.DOCUMENT_ROOTS at `root`, as a tag's document set holds them."""
    documents = {}
    for name in model.DOCUMENT_ROOTS:
        base = root / name
        paths = sorted(base.rglob("*")) if name.endswith("/") else [base]
        documents |= {p.relative_to(root).as_posix(): p.read_bytes() for p in paths if p.is_file()}
    return documents


def loaded(documents: dict[str, bytes]) -> Model:
    provenance = model.Provenance("git", TAG, "0" * 40, "Example Owner <owner@example.org>",
                                  "2026-01-01T00:00:00Z", model.set_sha256(documents), ())
    return model.load(DocumentSet(documents, provenance))


def edited(changes: dict[str, Callable[[Any], None]]) -> dict[str, bytes]:
    """The good set with each named document changed through its parsed form."""
    documents = good()
    for path, change in changes.items():
        doc = yaml.safe_load(documents[path])
        change(doc)
        documents[path] = yaml.safe_dump(doc, sort_keys=False).encode()
    return documents


def paused(product: Any) -> None:
    product["status"] = "paused"
    product["customProperties"].append({"property": "lifecycleChange", "value": {
        "since": "2026-11-26", "reason": "Nothing reads it.", "decision": "ADR-057", "resume": "Rebuilt from the tested gold."}})
    product["outputPorts"][0]["customProperties"] = [
        {"property": "serves", "value": {"engine": "clickhouse", "profile": "hot-load", "status": "paused"}}]


def serving(contract: Any) -> None:
    (build,) = [p for p in contract["customProperties"] if p["property"] == "build"]
    build["value"] = "serving"


def repository_products() -> dict[str, str]:
    """Each real product's catalog file; empty while contracts/ holds none."""
    documents = repository()
    if not any(model.document_kind(p) == "product" for p in documents):
        return {}
    return {p: text for p, text in catalog.emit(loaded(documents)).items() if p != model.CATALOG_INDEX}


def goldens(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted((root / "compiled").rglob("*.json"))}


def kind(path: str) -> str:
    return "catalog-index" if path == model.CATALOG_INDEX else "catalog-product"


def test_the_good_set_compiles_to_its_goldens() -> None:
    assert catalog.emit(loaded(good())) == goldens(GOLDEN)


def test_each_repository_product_compiles_to_its_golden() -> None:
    emitted = repository_products()
    if not emitted:
        pytest.skip("contracts/ holds no v2 product yet")
    kept = goldens(REPOSITORY)
    compared = {p: text for p, text in emitted.items() if p in kept}
    assert compared and compared == {p: kept[p] for p in compared}


def every_file() -> list[tuple[str, str]]:
    return sorted((catalog.emit(loaded(good())) | repository_products()).items())


@pytest.mark.parametrize(("path", "text"), every_file())
def test_every_file_validates_against_the_frozen_schema(path: str, text: str) -> None:
    assert model.schema_errors(kind(path), json.loads(text)) == []


def internal_names() -> list[re.Pattern[str]]:
    """A compose service or container as a host (`trino:8080`, `//lakekeeper`), an absolute path, a
    secret's file or mount, an object store URL."""
    compose = yaml.safe_load((model.REPO / model.COMPOSE).read_text())["services"]
    hosts = sorted(set(compose) | {s["container_name"] for s in compose.values() if "container_name" in s})
    secrets = [s.path.split("<", 1)[0] for s in model.SECRET_FILES] + [model.SECRETS_MOUNT, model.SECRETS_ENV, "password"]
    return ([re.compile(rf"(//{re.escape(h)}\b|\b{re.escape(h)}:[0-9])") for h in hosts]
            + [re.compile(re.escape(s), re.I) for s in secrets if s]
            + [re.compile(r"""(^|[\s"'(])/[A-Za-z0-9_.-]+/"""), re.compile(r"\b(s3|file)://"),
               re.compile(re.escape(model.RELEASES_MOUNT)), re.compile(re.escape(model.LANDING_BUCKET))])


@pytest.mark.parametrize(("path", "text"), every_file())
def test_no_base_name_secret_internal_name_or_rule_text(path: str, text: str) -> None:
    assert model.BASE_SUFFIX not in text
    for pattern in internal_names():
        assert not pattern.search(text), (path, pattern.pattern)
    # A policy is described, never written as the rule enforcing it: no filter text, and no
    # file group (`<fence>__<role>`) the rules match readers by.
    assert "current_timestamp" not in text.lower() and not re.search(r"\bIN \(", text, re.I)
    m = loaded(good())
    for p in m.products:
        for role in m.roles:
            assert model.group(p.name, role.name) not in text


def test_a_policy_in_words() -> None:
    doc = json.loads(catalog.emit(loaded(good()))[model.catalog_path("sensor-net.station-readings")])
    silver = doc["outputs"][1]
    assert [(r["role"], r["inWords"]) for r in silver["roles"]] == [
        ("full_reader", "every row and column"),
        ("delayed_reader", "only rows whose region is north or south and whose read_at is at least 20 minutes old; "
                           "operator_note read as null")]
    assert silver["roles"][1]["policies"] == {"delay": {"column": "read_at", "minutes": 20}, "mask": ["operator_note"],
                                              "rowScope": {"column": "region", "values": ["north", "south"]}}
    assert "policies" not in silver["roles"][0]
    assert silver["readers"] == [{"principal": "p.analyst-full", "role": "full_reader"},
                                 {"principal": "p.analyst-delayed", "role": "delayed_reader"}]


@pytest.mark.parametrize(("policy", "words"), [
    (model.RolePolicy(model.Delay("event_ts", 1), (), None), "only rows whose event_ts is at least 1 minute old"),
    (model.RolePolicy(None, ("a", "b", "c"), None), "a, b and c read as null"),
    (model.RolePolicy(None, (), model.RowScope("station_no", (7,))), "only rows whose station_no is 7"),
    (model.RolePolicy(None, ("note",), model.RowScope("region", ("east", "north", "west"))),
     "only rows whose region is east, north or west; note read as null"),
    (None, "every row and column"),
])
def test_each_policy_kind_in_words(policy: model.RolePolicy | None, words: str) -> None:
    assert catalog.in_words(policy) == words


def test_a_paused_product_with_a_serving_copy() -> None:
    m = loaded(edited({f"{DAILY}/product.odps.yaml": paused, f"{DAILY}/gold_daily_load.odcs.yaml": serving}))
    doc = json.loads(catalog.emit(m)[model.catalog_path("ops-insight.daily-load")])
    assert model.schema_errors("catalog-product", doc) == []
    assert doc["status"] == "paused"
    assert doc["lifecycleChange"] == {"since": "2026-11-26", "reason": "Nothing reads it.", "decision": "ADR-057",
                                      "resume": "Rebuilt from the tested gold."}
    (out,) = doc["outputs"]
    assert out["relation"] is None and out["dbtDocs"] is None
    assert out["serves"] == {"engine": "clickhouse", "profile": "hot-load", "status": "paused"}
    assert doc["joinKeys"] == []  # a serving copy has no reader name to join from
    assert out["columns"][1]["references"] == [{"relation": "sensor_net.station_readings.silver_readings", "column": "station_id"}]
    assert doc["lineage"]["nodes"][1] == {"id": "ops-insight.daily-load.gold_daily_load", "product": "ops-insight.daily-load", "relation": None}
    assert doc["lineage"]["edges"] == [{"from": "sensor-net.station-readings.silver_readings", "to": "ops-insight.daily-load.gold_daily_load"}]
    index = json.loads(catalog.emit(m)[model.CATALOG_INDEX])
    assert [e["status"] for e in index["products"]] == ["paused", "active"]


def test_a_join_key_carries_its_as_of() -> None:
    def as_of(contract: Any) -> None:
        contract["schema"][0]["properties"][1]["relationships"][0]["customProperties"] = [{"property": "asOf", "value": "valid"}]

    doc = json.loads(catalog.emit(loaded(edited({f"{DAILY}/gold_daily_load.odcs.yaml": as_of})))[
        model.catalog_path("ops-insight.daily-load")])
    silver = {"relation": "sensor_net.station_readings.silver_readings", "column": "station_id"}
    assert doc["outputs"][0]["columns"][1]["references"] == [silver | {"asOf": "valid"}]
    assert doc["joinKeys"] == [{"from": {"relation": "ops_insight.daily_load.gold_daily_load", "column": "station_id"},
                                "to": silver, "asOf": "valid"}]


def test_the_index_lists_every_product_once_sorted_by_id() -> None:
    m = loaded(good())
    for products in (m.products, m.products[::-1]):
        index = json.loads(catalog.emit(dataclasses.replace(m, products=products))[model.CATALOG_INDEX])
        assert [e["id"] for e in index["products"]] == ["ops-insight.daily-load", "sensor-net.station-readings"]
        assert index["tag"] == TAG
    with pytest.raises(ValueError, match="share an id"):
        catalog.emit(dataclasses.replace(m, products=m.products + m.products[:1]))


def test_the_output_is_deterministic() -> None:
    documents = good()
    first = catalog.emit(loaded(documents))
    assert catalog.emit(loaded(dict(reversed(documents.items())))) == first
    assert list(first) == [model.catalog_path("ops-insight.daily-load"), model.catalog_path("sensor-net.station-readings"),
                           model.CATALOG_INDEX]


def test_a_base_name_anywhere_is_refused() -> None:
    def example(product: Any) -> None:
        (prop,) = [p for p in product["customProperties"] if p["property"] == "examples"]
        prop["value"][0]["sql"] = "SELECT * FROM sensor_net.station_readings__base.silver_readings"

    with pytest.raises(ValueError, match="__base"):
        catalog.emit(loaded(edited({f"{STATION}/product.odps.yaml": example})))


def test_an_input_no_contract_declares_is_refused() -> None:
    def stray(product: Any) -> None:
        product["inputPorts"][0]["contractId"] = "sensor-net.station-readings.gone"

    with pytest.raises(ValueError, match="sensor-net.station-readings.gone, which no contract of the set declares"):
        catalog.emit(loaded(edited({f"{DAILY}/product.odps.yaml": stray})))


def regenerate(root: Path) -> None:
    for out, documents in ((GOLDEN, good()), (REPOSITORY, repository(root))):
        if out == REPOSITORY and not any(model.document_kind(p) == "product" for p in documents):
            continue
        for path, text in catalog.emit(loaded(documents)).items():
            if out == REPOSITORY and path == model.CATALOG_INDEX:
                continue  # the repository's index grows with every product; each product is pinned
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            (out / path).write_text(text)


if __name__ == "__main__":
    regenerate(Path(sys.argv[1]) if len(sys.argv) > 1 else model.REPO)
