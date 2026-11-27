"""ISO 4217 currency list ("list one" XML) -> one row per alphabetic currency code.

fetch calls parse(bytes) -> list of dicts; any raise is a parse failure (exit 30, bytes kept).
Standard library only; a parser cannot import a sibling module.
"""
import re
import xml.etree.ElementTree as ET

FIELDS = {"CtryNm", "CcyNm", "Ccy", "CcyNbr", "CcyMnrUnts"}


def parse(raw: bytes) -> list[dict]:
    head = raw.upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
        raise ValueError("DOCTYPE or ENTITY declaration refused")
    root = ET.fromstring(raw)
    tables = list(root)
    if root.tag != "ISO_4217" or len(tables) != 1 or tables[0].tag != "CcyTbl":
        raise ValueError("unexpected structure")
    found: dict[str, tuple] = {}
    for e in tables[0]:
        if e.tag != "CcyNtry" or any(c.tag not in FIELDS for c in e):
            raise ValueError("unexpected entry")
        f = {c.tag: c for c in e}
        if "Ccy" not in f:  # a territory with no currency (Antarctica)
            continue
        code = (f["Ccy"].text or "").strip()
        num = (f["CcyNbr"].text or "").strip() if "CcyNbr" in f else ""
        mnr = (f["CcyMnrUnts"].text or "").strip() if "CcyMnrUnts" in f else ""
        name = (f["CcyNm"].text or "").strip() if "CcyNm" in f else ""
        if not re.fullmatch(r"[A-Z]{3}", code) or not re.fullmatch(r"[0-9]{3}", num) or not name:
            raise ValueError(f"bad entry {code!r} {num!r}")
        if mnr != "N.A." and not mnr.isdigit():
            raise ValueError(f"bad minor units {mnr!r}")
        fund = f["CcyNm"].get("IsFund", "").lower() == "true"
        entry = (name, num, None if mnr == "N.A." else int(mnr), fund)
        if code in found and found[code][1:] != entry[1:]:
            raise ValueError(f"{code} disagrees across countries")
        found[code] = min(found.get(code, entry), entry)  # sorted choice, deterministic
    if not found:
        raise ValueError("no rows")
    return [{"code": c, "numeric_code": n, "name": nm, "minor_units": m, "is_fund": fd}
            for c, (nm, n, m, fd) in sorted(found.items())]
