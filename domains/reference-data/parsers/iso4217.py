"""ISO 4217 currency list ("list one" XML) -> one row per alphabetic currency code.

fetch calls parse(bytes) -> list of dicts; any raise is a parse failure (exit 30, bytes kept).
Standard library only; a parser cannot import a sibling module.
"""
import re
import xml.etree.ElementTree as ET
import xml.parsers.expat

FIELDS = {"CtryNm", "CcyNm", "Ccy", "CcyNbr", "CcyMnrUnts"}

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
