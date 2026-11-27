"""scripts/lib/kafka_seed.py's routing: each synthetic Kraken frame gets the stream and symbol the
Kraken adapter gives it (services/capture-rust/src/exchanges/kraken.rs), with no Spark."""

import collections
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("kafka_seed", ROOT / "scripts/lib/kafka_seed.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
FIXTURE = ROOT / "services/capture-rust/tests/fixtures/synthetic-kraken-20s.jsonl"


def test_route_mirrors_the_adapter():
    assert seed.route('{"channel":"trade","data":[{"symbol":"BTC/USD"},{"symbol":"BTC/USD"}]}') == ("trade", "BTC/USD")
    assert seed.route('{"channel":"book","data":[{"symbol":"BTC/USD"},{"symbol":"ETH/USD"}]}') == ("book", None)
    assert seed.route('{"method":"subscribe","result":{"channel":"trade","symbol":"BTC/USD"}}') == ("control", None)
    assert seed.route('{"channel":"heartbeat"}') == ("heartbeat", None)
    assert seed.route('{"type":"x"}') == ("unknown", None)
    assert seed.route("not json") == ("unparseable", None)
    assert seed.route("[1]") == ("unparseable", None)


def test_every_fixture_line_is_one_frame_numbered_from_one():
    rows = seed.frames(str(FIXTURE))
    assert len(rows) == sum(1 for x in FIXTURE.read_text().splitlines() if x.strip())
    assert [r[5] for r in rows] == list(range(1, len(rows) + 1))
    streams = collections.Counter(r[1] for r in rows)
    assert streams["trade"] == 3 and streams["control"] == 3 and "unparseable" not in streams
    assert {r[2] for r in rows if r[1] == "trade"} == {"BTC/USD"}
