"""
Structural tests for the v3 data contracts: schemas/avro/*.avsc and
config/instruments.yaml.

These are the guards for failures that are *silent* rather than loud. Every rule
here corresponds to a mistake that parses, validates, registers and serialises
cleanly, and only shows up as wrong numbers somewhere downstream:

  - `logicalType` as a sibling of `type` — Avro ignores it and the field
    degrades to a bare long. This exact bug shipped in v2
    (normalized-trade.avsc:60) and nothing caught it for six months.
  - a price or quantity that is not a fixed-point `long` — a float on the wire,
    or a decimal logicalType nobody wired up.
  - a nullable field with no default — breaks BACKWARD_TRANSITIVE reads of older
    data, which only surfaces when someone queries last year's Iceberg files.
  - a field with no `doc` — the scale of a fixed-point integer lives in the doc
    string and nowhere else, so an undocumented field is an unusable one.
  - a duplicate canonical symbol, or a canonical that is not BASE/QUOTE — two
    instruments silently collapsing onto one partition key.

No Avro library and no running stack on purpose: this gates a PR, not a deploy.
Round-trip encode/decode against a live registry belongs to the capture tier's
own tests (Phase C).
"""

import json
import re
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
AVRO_DIR = PROJECT_ROOT / "schemas" / "avro"
INSTRUMENTS_YAML = PROJECT_ROOT / "config" / "instruments.yaml"

# The v3 contract. normalized-trade.avsc is the superseded v2 schema and is
# deliberately excluded — it fails several rules below, which is the point.
V3_SCHEMAS = {
    "raw-message.avsc": "RawMessage",
    "trade.avsc": "Trade",
    "book-snapshot-l2.avsc": "BookSnapshotL2",
}

# The nullable (union) fields each v3 record is allowed to have. Pinned so
# test_nullable_fields_default_to_null cannot pass vacuously — see its docstring.
EXPECTED_NULLABLE = {
    "raw-message.avsc": {"symbol"},
    "trade.avsc": set(),
    "book-snapshot-l2.avsc": {"checksum_ok", "exchange_ts"},
}

CANONICAL_RE = re.compile(r"^[A-Z0-9]+/[A-Z0-9]+$")

# Fixed-point int64 at scale 1e-8. Matched by name because that is the only
# thing a schema reader has to go on; the naming convention is the contract.
FIXED_POINT_FIELDS = {"price", "qty"}
FIXED_POINT_SUFFIXES = ("_px", "_qty")

AVRO_PRIMITIVES = {"null", "boolean", "int", "long", "float", "double", "bytes", "string"}

EXPECTED_COUNTS = {"binance": 12, "kraken": 11, "coinbase": 11}


def _load(filename: str) -> dict:
    return json.loads((AVRO_DIR / filename).read_text())


def _walk(node):
    """Yield every dict in the schema tree, outermost first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _fields(schema: dict) -> list:
    """Every field dict anywhere in the schema, including nested records."""
    return [f for node in _walk(schema) for f in node.get("fields", [])]


def _is_fixed_point(name: str) -> bool:
    return name in FIXED_POINT_FIELDS or name.endswith(FIXED_POINT_SUFFIXES)


@pytest.fixture(autouse=True)
def mock_prefect_run_logger():
    """
    Neutralise the autouse fixture of the same name in tests/conftest.py.

    That one patches `prefect.get_run_logger` for the offload-flow tests, which
    makes Prefect a hard import for every test in this directory. Nothing in
    this module touches Prefect, ClickHouse or a running stack, and it should
    stay runnable with nothing but pytest and pyyaml — a same-named fixture in
    a test module overrides the conftest one, autouse included.
    """
    yield


@pytest.fixture(scope="module")
def instruments() -> dict:
    return yaml.safe_load(INSTRUMENTS_YAML.read_text())


# ─────────────────────────────────────────────────────────────────────────────
# Avro schemas
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("filename,record_name", sorted(V3_SCHEMAS.items()))
def test_schema_is_a_named_v3_record(filename, record_name):
    schema = _load(filename)
    assert schema["type"] == "record"
    assert schema["name"] == record_name
    assert schema["namespace"] == "com.k2.market.v3"
    assert schema.get("doc"), f"{filename}: the record itself needs a doc"


@pytest.mark.parametrize("filename", sorted(V3_SCHEMAS))
def test_every_field_has_a_doc(filename):
    for field in _fields(_load(filename)):
        doc = field.get("doc")
        assert doc and doc.strip(), f"{filename}: field '{field['name']}' has no doc"


@pytest.mark.parametrize("filename", sorted(V3_SCHEMAS))
def test_logical_type_is_never_a_sibling_of_type(filename):
    """
    The v2 bug. `{"name": x, "type": "long", "logicalType": "timestamp-micros"}`
    parses fine and Avro throws the logicalType away. It belongs one level in:
    `{"name": x, "type": {"type": "long", "logicalType": "timestamp-micros"}}`.
    """
    for field in _fields(_load(filename)):
        assert "logicalType" not in field, (
            f"{filename}: field '{field['name']}' has logicalType as a sibling of "
            "type — Avro ignores it there. Nest it inside the type object."
        )


@pytest.mark.parametrize("filename", sorted(V3_SCHEMAS))
def test_logical_types_annotate_a_primitive_type_object(filename):
    for node in _walk(_load(filename)):
        if "logicalType" not in node:
            continue
        assert "name" not in node, (
            f"{filename}: logicalType '{node['logicalType']}' sits on a named "
            "declaration rather than on a type object"
        )
        assert node.get("type") in AVRO_PRIMITIVES, (
            f"{filename}: logicalType '{node['logicalType']}' annotates "
            f"{node.get('type')!r}, which is not an Avro primitive"
        )


def test_timestamps_are_micros_on_a_long():
    """
    Every timestamp in the v3 contract is timestamp-micros on a long. Micros
    because ClickHouse 24.3 AvroConfluent decodes that to DateTime64(6);
    millis would silently truncate Kraken and Coinbase, both of which publish
    sub-millisecond trade times.
    """
    found = 0
    for filename in V3_SCHEMAS:
        for node in _walk(_load(filename)):
            logical = node.get("logicalType", "")
            if not logical.startswith("timestamp"):
                continue
            found += 1
            assert logical == "timestamp-micros", f"{filename}: got {logical}"
            assert node["type"] == "long", f"{filename}: timestamp on {node['type']!r}"
    assert found >= 2, "expected exchange_ts on both Trade and BookSnapshotL2"


@pytest.mark.parametrize("filename", sorted(V3_SCHEMAS))
def test_prices_and_quantities_are_fixed_point_longs(filename):
    """
    Fixed-point int64 at 1e-8, scalar or in an array. Never float, never double,
    never a decimal logicalType — see schemas/README.md for why.
    """
    found = 0
    for field in _fields(_load(filename)):
        if not _is_fixed_point(field["name"]):
            continue
        found += 1
        declared = field["type"]
        if isinstance(declared, dict) and declared.get("type") == "array":
            actual = declared["items"]
        else:
            actual = declared
        assert actual == "long", (
            f"{filename}: fixed-point field '{field['name']}' is {actual!r}, "
            "must be a bare long holding value * 1e8"
        )

    if filename != "raw-message.avsc":
        assert found, f"{filename}: no price/quantity fields found — did one get renamed?"


@pytest.mark.parametrize("filename", sorted(V3_SCHEMAS))
def test_nullable_fields_default_to_null(filename):
    """
    BACKWARD_TRANSITIVE needs a default on every nullable field, and Avro only
    honours a default that matches the union's *first* branch — so the union
    must lead with "null" and the default must be null.

    The expected union fields are pinned per schema rather than left implicit.
    Without that this test passes trivially on any schema whose unions were
    accidentally flattened to a bare type — the exact regression it exists to
    catch. `trade.avsc` legitimately has none: every Trade field is required,
    which is itself a contract worth failing on if it ever changes.
    """
    nullable = {f["name"] for f in _fields(_load(filename)) if isinstance(f["type"], list)}
    assert nullable == EXPECTED_NULLABLE[filename], (
        f"{filename}: nullable fields are {sorted(nullable)}, expected "
        f"{sorted(EXPECTED_NULLABLE[filename])} — a union that silently became a "
        "bare type breaks BACKWARD_TRANSITIVE reads of older data"
    )

    for field in _fields(_load(filename)):
        declared = field["type"]
        if not isinstance(declared, list):
            continue
        assert declared[0] == "null", (
            f"{filename}: union field '{field['name']}' must list null first"
        )
        assert "default" in field and field["default"] is None, (
            f"{filename}: nullable field '{field['name']}' needs \"default\": null"
        )


def test_lineage_fields_are_on_every_record():
    """
    recv_ts_ns + conn_id + conn_msg_seq are what make completeness provable and
    what tie a derived row back to the raw frame it came from. A record that
    loses them cannot be audited, so this is the one cross-schema invariant.
    """
    for filename in V3_SCHEMAS:
        names = {f["name"] for f in _fields(_load(filename))}
        assert {"recv_ts_ns", "conn_id", "conn_msg_seq"} <= names, filename


def test_trade_side_is_a_lowercase_enum():
    side = next(f for f in _fields(_load("trade.avsc")) if f["name"] == "side")
    assert side["type"]["type"] == "enum"
    assert side["type"]["symbols"] == ["buy", "sell"]


def test_raw_payload_is_bytes():
    """
    bytes, not string: a string forces UTF-8 validation on every hop and would
    corrupt a malformed frame — which is exactly the frame worth archiving.
    """
    payload = next(f for f in _fields(_load("raw-message.avsc")) if f["name"] == "payload")
    assert payload["type"] == "bytes"


def test_book_snapshot_arrays_are_parallel_longs():
    fields = {f["name"]: f["type"] for f in _fields(_load("book-snapshot-l2.avsc"))}
    for name in ("bid_px", "bid_qty", "ask_px", "ask_qty"):
        assert fields[name] == {"type": "array", "items": "long"}, name


def test_checksum_ok_is_three_valued():
    """
    null = the venue publishes no checksum (Binance, Coinbase), true = verified,
    false = the local book drifted. Collapsing null into true would claim two
    venues' books were verified when nothing verified them.
    """
    field = next(f for f in _fields(_load("book-snapshot-l2.avsc")) if f["name"] == "checksum_ok")
    assert field["type"] == ["null", "boolean"]
    assert field["default"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Instrument registry
# ─────────────────────────────────────────────────────────────────────────────


def test_registry_is_version_2(instruments):
    assert instruments["version"] == 2


def test_registry_holds_exactly_34_instruments(instruments):
    per_exchange = {ex: len(rows) for ex, rows in instruments["instruments"].items()}
    assert per_exchange == EXPECTED_COUNTS
    assert sum(per_exchange.values()) == 34


@pytest.mark.parametrize("exchange", sorted(EXPECTED_COUNTS))
def test_every_canonical_is_base_slash_quote(instruments, exchange):
    for row in instruments["instruments"][exchange]:
        canonical = row["canonical"]
        assert CANONICAL_RE.match(canonical), f"{exchange}: {canonical!r} is not BASE/QUOTE"


@pytest.mark.parametrize("exchange", sorted(EXPECTED_COUNTS))
def test_no_duplicate_canonical_per_exchange(instruments, exchange):
    """
    Two natives mapping to one canonical on the same venue means two order books
    merged onto one partition key. Kraken is the live risk: XBT/USD and XDG/USD
    both rename, and adding a BTC/USD alongside XBT/USD would collide silently.
    """
    canonicals = [row["canonical"] for row in instruments["instruments"][exchange]]
    duplicates = {c for c in canonicals if canonicals.count(c) > 1}
    assert not duplicates, f"{exchange}: duplicate canonical symbols {duplicates}"


@pytest.mark.parametrize("exchange", sorted(EXPECTED_COUNTS))
def test_no_duplicate_native_per_exchange(instruments, exchange):
    natives = [row["native"] for row in instruments["instruments"][exchange]]
    assert len(natives) == len(set(natives)), f"{exchange}: duplicate native symbols"


@pytest.mark.parametrize("exchange", sorted(EXPECTED_COUNTS))
def test_every_instrument_declares_native_and_canonical(instruments, exchange):
    for row in instruments["instruments"][exchange]:
        assert set(row) <= {"native", "canonical", "book_depth"}, f"{exchange}: {row}"
        assert row["native"] and row["canonical"]
        if "book_depth" in row:
            assert isinstance(row["book_depth"], int) and row["book_depth"] > 0


def test_quote_currency_is_kept_as_the_exchange_quotes_it(instruments):
    """
    BTC/USDT and BTC/USD are different instruments and must never be folded
    together: different venues, different collateral, and the basis between them
    is itself a research subject. Binance is USDT-margined; Kraken and Coinbase
    quote USD.
    """
    quotes = {
        ex: {row["canonical"].split("/")[1] for row in rows}
        for ex, rows in instruments["instruments"].items()
    }
    assert quotes["binance"] == {"USDT"}
    assert quotes["kraken"] == {"USD"}
    assert quotes["coinbase"] == {"USD"}


def test_venue_private_tickers_are_renamed(instruments):
    """
    The other half of the rule: the same instrument under a venue's private
    ticker DOES get folded. Kraken's XBT is Bitcoin and XDG is Dogecoin.
    """
    kraken = {row["native"]: row["canonical"] for row in instruments["instruments"]["kraken"]}
    assert kraken["XBT/USD"] == "BTC/USD"
    assert kraken["XDG/USD"] == "DOGE/USD"
    assert "XBT" not in " ".join(kraken.values())
    assert "XDG" not in " ".join(kraken.values())


def test_cross_venue_instruments_share_one_canonical(instruments):
    """
    BTC/USD on Kraken and BTC/USD on Coinbase must key identically, otherwise a
    cross-venue join is impossible — which is most of why canonical exists.
    """
    by_exchange = {
        ex: {row["canonical"] for row in rows} for ex, rows in instruments["instruments"].items()
    }
    shared = by_exchange["kraken"] & by_exchange["coinbase"]
    assert len(shared) == 11, "Kraken and Coinbase should cover the same 11 USD pairs"
    assert "BTC/USD" in shared and "DOGE/USD" in shared
