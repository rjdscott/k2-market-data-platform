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


# The spellings YAML 1.2 reads as a boolean, which is what the capture's
# serde_yaml accepts. PyYAML is YAML 1.1 and also reads `on`, `off`, `yes`, `no`
# as booleans, which the capture refuses: so they are refused here too, and the
# lake and the capture cannot read one file two ways.
_YAML12_BOOLEANS = {"true", "True", "TRUE", "false", "False", "FALSE"}


def book_capture(path: Path = INSTRUMENTS_PATH) -> dict:
    """`{venue: bool}`, the registry's order-book switch (ADR-050).

    The capture's rules (services/capture-rust/src/config.rs): the section is
    required, names every venue the registry lists and no other, and each value
    is a YAML 1.2 boolean. Anything else raises: a default would turn a missing
    line into a silent "on".
    """
    text = path.read_text()
    doc = yaml.safe_load(text)
    section = doc.get("book_capture")
    if not isinstance(section, dict):
        raise ValueError(f"{path}: book_capture is required, a true or false for every venue (ADR-050)")
    venues = set(doc["instruments"])
    if set(section) != venues:
        raise ValueError(
            f"{path}: book_capture names {sorted(section)}, the registry's venues are {sorted(venues)}"
        )
    (node,) = [v for k, v in yaml.compose(text).value if k.value == "book_capture"]
    for key, value in node.value:
        if not isinstance(section[key.value], bool) or value.value not in _YAML12_BOOLEANS:
            raise ValueError(f"{path}: book_capture.{key.value} is {value.value!r}, not true or false")
    return section


def canonical(registry: dict, exchange: str, native: str) -> str:
    try:
        return registry[exchange][native]
    except KeyError as exc:
        raise UnknownInstrument(f"{exchange}: native symbol {native!r} is not in instruments.yaml") from exc
