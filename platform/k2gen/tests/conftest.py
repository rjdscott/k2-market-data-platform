"""The trees the k2gen tests break and generate from.

No product has a contract in the repository today: the old ones retired with the old lake
ingest to legacy/v3-lake-ingest/ (ADR-056). So `bare` is the repository's contracts/ (the
domains and the schemas) and docs/adr/, and `tree` adds two products made by `k2gen new`
and wired together: a source-aligned bronze table fed by an external source, and a
consumer-aligned gold table that reads it, promises a latency and has a mart. Every check
then has something real to fire on.
"""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import cli, generate

FRAMES = "contracts/market-capture/example-frames"
FRAMES_CONTRACT = f"{FRAMES}/bronze.example_frames.odcs.yaml"
TRADES = "contracts/market-analytics/example-trades"
TRADES_PRODUCT = f"{TRADES}/product.yaml"
TRADES_CONTRACT = f"{TRADES}/gold.example_trades.odcs.yaml"
MART = "market_analytics.example_trades.example_trades"
EXTERNAL = "synthetic venue feed"


def edit(path: Path, change: Callable[[Any], Any]) -> None:
    doc = yaml.safe_load(path.read_text())
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def set_property(name: str, value: Any) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        (prop,) = [p for p in doc["customProperties"] if p["property"] == name]
        prop["value"] = value

    return change


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    """contracts/ and docs/adr/ as the repository has them, and nothing generated."""
    shutil.copytree(cli.ROOT / "contracts", tmp_path / "contracts")
    shutil.copytree(cli.ROOT / cli.ADR_DIR, tmp_path / cli.ADR_DIR)  # a paused contract names its ADR
    return tmp_path


@pytest.fixture
def tree(bare: Path) -> Path:
    """bare, plus bronze.example_frames <- EXTERNAL and gold.example_trades <- it, with MART."""
    generate.scaffold(bare, "market-capture", "example-frames")
    generate.scaffold(bare, "market-analytics", "example-trades")
    feed = {"external": EXTERNAL, "classification": "internal", "description": "Made-up frames, as a venue would send them."}
    edit(bare / FRAMES / "product.yaml", lambda d: d.update(inputs=[feed]))
    edit(bare / FRAMES_CONTRACT, set_property("upstreams", [EXTERNAL]))
    mart = {"table": MART, "grain": ["id"], "reads": ["gold.example_trades"]}
    edit(bare / TRADES_PRODUCT, lambda d: d.update(inputs=[{"table": "bronze.example_frames"}], marts=[mart]))

    def trades(doc: Any) -> None:
        set_property("upstreams", ["bronze.example_frames"])(doc)
        doc["slaProperties"] = [{"property": "latency", "value": 30, "unit": "min", "element": "gold.example_trades.event_ts"}]

    edit(bare / TRADES_CONTRACT, trades)
    assert cli.validate(bare) == []
    return bare
