"""A batch with no frame for a venue must not fail the ingest.

On 2026-11-26 the first run after Binance's order book was paused (ADR-050) failed at
stage 2e: `create_map()` with no entries is map<void,void>, and indexing it by the
symbol column is a type error. Spark is not available to these tests, so this pins the
source: every symbol map in docker/lake is cast to a typed map before it is indexed.
The behaviour itself was probed in the Spark container (empty and non-empty frames).
"""
import re
from pathlib import Path

LAKE = Path(__file__).resolve().parent.parent / "docker" / "lake"


def test_every_symbol_map_is_typed_before_it_is_indexed():
    found = 0
    for path in LAKE.glob("*.py"):
        for line in path.read_text().splitlines():
            if re.search(r"F\.create_map\(", line) and not line.lstrip().startswith("#"):
                found += 1
                assert '.cast("map<string,string>")' in line, f"{path.name}: {line.strip()}"
    assert found == 2, "books.py and silver.py each build one symbol map"
