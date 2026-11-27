"""k2gen.checks.connectors (W1-2): the kinds overlay passes every check, each failing case fails
with exactly its code, and every code has a case."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import checks, model
from k2gen.checks import connectors
from k2gen.model import DocumentSet, Finding, Provenance

FIXTURES = Path(__file__).parent / "fixtures" / "v2"
FEED, LIST = "contracts/sensor-net/station-feed", "contracts/sensor-net/station-list"
FEED_ID, LIST_ID = "sensor-net.station-feed", "sensor-net.station-list"
# The access check's denied rows for the overlay's bronze and landing tables, added to the good set's.
DENIED = ("lake.station_feed.bronze_frames", "lake.station_feed.landing", "lake.station_list.bronze_stations",
          "lake.station_list.landing_files")
PROVENANCE = Provenance(source="git", tag="v0.0.0-fixture", commit="0" * 40, tagger="fixture",
                        tagged_at="2026-11-27T00:00:00Z", set_sha256="0" * 64, documents=())


def kinds() -> dict[str, bytes]:
    """The good set with the kinds overlay laid on it: fixtures/v2/kinds/ holds one Kafka product
    (station-feed) and one public-API product (station-list), synthetic, and the minimal compose
    file the Kafka host check reads; kept out of the good set so its goldens stay as they are."""
    out = {}
    for base in (FIXTURES / "good", FIXTURES / "kinds"):
        out |= {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}
    rows = "".join(f"  - {{principal: p.analyst-full, table: {t}, denied: true}}\n" for t in DENIED)
    out[model.EXPECTATIONS] += rows.encode()
    return out


def edited(path: str, change: Callable[[Any], None], documents: dict[str, bytes] | None = None) -> dict[str, bytes]:
    documents = kinds() if documents is None else documents
    doc = model.parse(path, documents[path])
    change(doc)
    documents[path] = yaml.safe_dump(doc, sort_keys=False).encode()
    return documents


def connector(**changes: Any) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        (c,) = [p["value"] for p in doc["customProperties"] if p["property"] == "connector"]
        for k, v in changes.items():
            if v is None:
                del c[k]
            else:
                c[k] = v
    return change


def run(documents: dict[str, bytes]) -> list[Finding]:
    return checks.run(DocumentSet(documents, PROVENANCE), None)


def _second_bronze(documents: dict[str, bytes]) -> dict[str, bytes]:
    documents[f"{FEED}/bronze_copies.odcs.yaml"] = documents[f"{FEED}/bronze_frames.odcs.yaml"].replace(b"bronze_frames", b"bronze_copies")
    documents[model.EXPECTATIONS] += b"  - {principal: p.analyst-full, table: lake.station_feed.bronze_copies, denied: true}\n"
    return edited(f"{FEED}/product.odps.yaml", lambda d: d["outputPorts"].append(
        {"name": "bronze_copies", "version": "1.0.0", "contractId": f"{FEED_ID}.bronze_copies"}), documents)


def _column(change: Callable[[list], None]) -> Callable[[Any], None]:
    return lambda doc: change(doc["schema"][0]["properties"])


def _swap_key(props: list) -> None:
    props[0]["primaryKeyPosition"], props[1]["primaryKeyPosition"] = 2, 1


CASES = {
    "kafka bronze with no envelope column": ("connectors.kafka-bronze-shape", lambda: edited(
        f"{FEED}/bronze_frames.odcs.yaml", _column(lambda p: [p.pop(4) for _ in range(2)]))),
    "kafka bronze still holding the frame's bytes": ("connectors.kafka-bronze-shape", lambda: edited(
        f"{FEED}/bronze_frames.odcs.yaml", _column(lambda p: p.insert(4, {"name": "record_value", "physicalType": "varbinary", "description": "The bytes."})))),
    "kafka bronze with a required envelope column": ("connectors.kafka-bronze-shape", lambda: edited(
        f"{FEED}/bronze_frames.odcs.yaml", _column(lambda p: p[4].update(required=True)))),
    "kafka bronze with a decoded column after its lineage": ("connectors.kafka-bronze-shape", lambda: edited(
        f"{FEED}/bronze_frames.odcs.yaml", _column(lambda p: p.append({"name": "station_id", "physicalType": "varchar", "description": "Decoded."})))),
    "kafka bronze without a lineage column": ("connectors.kafka-bronze-shape", lambda: edited(
        f"{FEED}/bronze_frames.odcs.yaml", _column(lambda p: p.pop()))),
    "kafka bronze with a retyped column": ("connectors.kafka-bronze-shape", lambda: edited(
        f"{FEED}/bronze_frames.odcs.yaml", _column(lambda p: p[3].update(physicalType="varchar")))),
    "kafka bronze keyed offset first": ("connectors.kafka-bronze-shape", lambda: edited(
        f"{FEED}/bronze_frames.odcs.yaml", _column(_swap_key))),
    "kafka product with two bronze tables": ("connectors.kafka-bronze-shape", lambda: _second_bronze(kinds())),
    "envelope contract undeclared": ("connectors.envelope-unresolved", lambda: edited(
        f"{FEED}/product.odps.yaml", connector(envelope={"format": "avro-confluent", "contract": f"{FEED_ID}.raw_message"}))),
    "broker outside the stack": ("connectors.host-outside-stack", lambda: edited(
        f"{FEED}/product.odps.yaml", connector(brokers="redpanda:9092,broker.example.org:9092"))),
    "registry on another network": ("connectors.host-outside-stack", lambda: edited(
        f"{FEED}/product.odps.yaml", connector(registry="http://minio-public:8081"))),
    "no compose file to resolve hosts": ("connectors.host-outside-stack", lambda: {
        k: v for k, v in kinds().items() if k != model.COMPOSE}),
    "next-link pagination": ("connectors.pagination-unsupported", lambda: edited(
        f"{LIST}/product.odps.yaml", connector(pagination="next-link", maxPages=5))),
    "page pagination": ("connectors.pagination-unsupported", lambda: edited(
        f"{LIST}/product.odps.yaml", connector(pagination="page", maxPages=5))),
    "an auth header": ("connectors.auth-unsupported", lambda: edited(
        f"{LIST}/product.odps.yaml", connector(authHeaderEnv="K2_STATION_TOKEN"))),
    "url host not allowed": ("connectors.url-host-not-allowed", lambda: edited(
        f"{LIST}/product.odps.yaml", connector(allowHosts=["cdn.example.org"]))),
    "https-file url host not allowed": ("connectors.url-host-not-allowed", lambda: edited(
        "contracts/sensor-net/station-readings/product.odps.yaml", connector(allowHosts=["api.example.org"]))),
}


def test_the_kinds_overlay_and_the_good_set_have_no_finding() -> None:
    assert run(kinds()) == []
    assert connectors.check(DocumentSet(kinds(), PROVENANCE), None) == []


@pytest.mark.parametrize("name", CASES)
def test_each_failing_case_yields_exactly_its_code(name: str) -> None:
    code, documents = CASES[name]
    found = run(documents())
    assert {f.code for f in found} == {code}, found
    assert all(f.message.startswith("$") for f in found), found


def test_every_code_has_a_failing_case() -> None:
    assert {code for code, _ in CASES.values()} == set(connectors.CODES)


def test_the_pagination_refusal_says_what_the_contract_cannot_state() -> None:
    (f,) = run(edited(f"{LIST}/product.odps.yaml", connector(pagination="page", maxPages=5)))
    assert "page parameter's name and first value" in f.message and "use none" in f.message


def test_the_repository_compose_puts_redpanda_beside_the_loader() -> None:
    """The live compose file: the one broker service shares a network with the load's service."""
    hosts = connectors._stack_hosts((model.REPO / model.COMPOSE).read_bytes())
    assert hosts is not None and "redpanda" in hosts and "fetch" not in hosts
