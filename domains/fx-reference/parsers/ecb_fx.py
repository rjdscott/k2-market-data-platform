"""ECB euro foreign exchange reference rates (eurofxref-hist-90d.xml) -> one row per (date, currency).

fetch calls parse(bytes) -> list of dicts; any raise is a parse failure (exit 30, bytes kept).
Rates stay the published strings: decimal precision is the model's job, never a float here.
Standard library only; a parser cannot import a sibling module.

The audits, rate_positive and no_future_date, are the bronze table's declared parser audits:
fetch calls each on the parsed rows (`def <audit>(rows) -> list[str]`, one description per
failing row, [] passes) and the connector's audit task records the result.
"""
import datetime
import decimal
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


def today() -> datetime.date:
    """The UTC date now; the tests replace it to hold the clock still."""
    return datetime.datetime.now(datetime.timezone.utc).date()


def rate_positive(rows: list[dict]) -> list[str]:
    """A reference rate is units of a currency per euro, so above zero; zero or below is corrupt."""
    return [f"{r['rate_date']} {r['currency']}: rate {r['rate']} is not above zero"
            for r in rows if decimal.Decimal(r["rate"]) <= 0]


def no_future_date(rows: list[dict]) -> list[str]:
    """No fixing is dated after today in UTC. Today itself is allowed: the newest date of a file
    fetched after the 16:00 CET publication is today, and CET is never behind UTC."""
    now = today()
    return [f"{r['rate_date']} {r['currency']}: dated after today ({now}, UTC)"
            for r in rows if datetime.date.fromisoformat(r["rate_date"]) > now]
