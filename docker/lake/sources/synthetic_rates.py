"""Parser of the synthetic test source: invented rates for invented codes (ADR-036).

Used only by tests and the integration proof of the file-source pattern. Its
contract lives under platform/k2gen/tests/synthetic/, never under contracts/,
so no DAG and no lake table exist for it in production. The format:

    <rates>
      <day date="2026-01-05">
        <rate code="AAA" value="1.2300"/>
      </day>
    </rates>
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal


def parse(data: bytes) -> list:
    root = ET.fromstring(data)
    if root.tag != "rates":
        raise ValueError(f"root element is <{root.tag}>, not <rates>")
    return [
        {"rate_date": date.fromisoformat(day.attrib["date"]), "code": rate.attrib["code"], "value": rate.attrib["value"]}
        for day in root.iter("day")
        for rate in day.iter("rate")
    ]


def positive_values(rows: list) -> list:
    """A product audit: every rate is above zero."""
    return [f"{r['rate_date']} {r['code']}: value {r['value']} is not positive" for r in rows if Decimal(r["value"]) <= 0]
