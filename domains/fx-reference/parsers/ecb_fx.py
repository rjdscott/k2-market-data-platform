"""ECB euro foreign exchange reference rates (eurofxref-hist-90d.xml) -> one row per (date, currency).

fetch calls parse(bytes) -> list of dicts; any raise is a parse failure (exit 30, bytes kept).
Rates stay the published strings: decimal precision is the model's job, never a float here.
Standard library only; a parser cannot import a sibling module.
"""
import datetime
import re
import xml.etree.ElementTree as ET

NS = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}"
ENVELOPE = "{http://www.gesmes.org/xml/2002-08-01}Envelope"
CURRENCY = re.compile(r"[A-Z]{3}")
RATE = re.compile(r"-?[0-9]+(\.[0-9]+)?")  # plain decimal; a negative rate is the audit's to catch


def parse(raw: bytes) -> list[dict]:
    # bytes come from the network: refuse DTDs and entities before any parse
    head = raw.upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError("DOCTYPE or ENTITY declaration refused")
    root = ET.fromstring(raw)
    if root.tag != ENVELOPE:
        raise ValueError(f"unexpected root {root.tag}")
    outer = root.findall(f"{NS}Cube")
    if len(outer) != 1:
        raise ValueError("expected exactly one top-level Cube")
    rows, seen = [], set()
    for day in outer[0]:
        if day.tag != f"{NS}Cube" or set(day.attrib) != {"time"}:
            raise ValueError("unexpected day element")
        date = datetime.date.fromisoformat(day.attrib["time"]).isoformat()
        for c in day:
            if c.tag != f"{NS}Cube" or set(c.attrib) != {"currency", "rate"} or len(c):
                raise ValueError("unexpected rate element")
            code, rate = c.attrib["currency"], c.attrib["rate"]
            if not CURRENCY.fullmatch(code):
                raise ValueError(f"bad currency {code!r}")
            if not RATE.fullmatch(rate):
                raise ValueError(f"bad rate {rate!r}")
            if (date, code) in seen:
                raise ValueError(f"duplicate {date} {code}")
            seen.add((date, code))
            rows.append({"rate_date": date, "currency": code, "rate": rate})
    if not rows:
        raise ValueError("no rows")
    return rows
