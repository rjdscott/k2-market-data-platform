#!/usr/bin/env python3
"""
The instrument registry (config/instruments.yaml) as silver needs it: the
native -> canonical symbol map per venue, and nothing else.

Pure — no Spark. `canonical()` raises on an unknown native symbol on purpose:
the registry's own contract (its header comment) is that a loader which cannot
find an instrument fails loudly rather than guesses, because guessing is what
produced `XDG/USD` and `DOGE/USD` as two instruments in v2. Silver would rather
stop than write a row whose canonical symbol is a fabrication.

tests/test_lake_instruments.py runs it against the real file.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# Mounted read-only into the Spark container by docker-compose.yml.
INSTRUMENTS_PATH = Path(os.environ.get("K2_INSTRUMENTS_PATH", "/home/iceberg/config/instruments.yaml"))


class UnknownInstrument(KeyError):
    """A native symbol the registry does not list for that venue."""


def load(path: Path = INSTRUMENTS_PATH) -> dict:
    """`{exchange: {native: canonical}}` from the registry file."""
    doc = yaml.safe_load(path.read_text())
    if doc.get("version") != 2:
        raise ValueError(f"{path}: expected instruments.yaml version 2, got {doc.get('version')!r}")
    out = {}
    for exchange, entries in doc["instruments"].items():
        out[exchange] = {e["native"]: e["canonical"] for e in entries}
    return out


def canonical(registry: dict, exchange: str, native: str) -> str:
    try:
        return registry[exchange][native]
    except KeyError as exc:
        raise UnknownInstrument(f"{exchange}: native symbol {native!r} is not in instruments.yaml") from exc
