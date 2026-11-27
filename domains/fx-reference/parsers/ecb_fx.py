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
import xml.parsers.expat

NS = "{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}"
ENVELOPE = "{http://www.gesmes.org/xml/2002-08-01}Envelope"
CURRENCY = re.compile(r"[A-Z]{3}")
RATE = re.compile(r"-?[0-9]+(\.[0-9]+)?")  # plain decimal; a negative rate is the audit's to catch

# Untrusted bytes from the network, made safe for expat. Duplicated in each parser by design:
# fetch loads a parser by its file path, so a parser cannot import a sibling module.
PROLOG = re.compile(r"""<\?xml\s[^>]*?\bencoding\s*=\s*(["'])(.*?)\1""")


def _refused(*_) -> None:
    raise ValueError("DOCTYPE or ENTITY declaration refused")


def _text(raw: bytes) -> str:
    """The document as text: UTF-8 (a UTF-8 BOM allowed) declaring no other encoding, with no
    DOCTYPE and no entity declaration, as expat itself reports them on this same text. The caller
    parses this text, never the bytes, so expat cannot switch to an encoding these checks did not read."""
    text = raw.decode("utf-8-sig")  # strict: UTF-16 or UTF-32 with a BOM, Latin-1 and the rest raise
    if "\x00" in text:
        raise ValueError("a NUL character: not UTF-8 (UTF-16 without a BOM?)")
    if (m := PROLOG.match(text)) and m[2].lower() not in ("utf-8", "us-ascii", "ascii"):
        raise ValueError(f"declared encoding {m[2]!r} refused: UTF-8 only")
    check = xml.parsers.expat.ParserCreate()  # ElementTree's C parser does not expose its expat handlers
    check.StartDoctypeDeclHandler = check.EntityDeclHandler = _refused
    try:
        check.Parse(text, True)  # a str is parsed as UTF-8, whatever the prolog says
    except xml.parsers.expat.ExpatError as e:
        raise ValueError(f"not well-formed XML: {e}") from None
    return text


def parse(raw: bytes) -> list[dict]:
    root = ET.fromstring(_text(raw))
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
