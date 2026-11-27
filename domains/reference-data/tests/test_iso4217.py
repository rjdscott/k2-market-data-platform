import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("k2_iso4217", HERE.parent / "parsers" / "iso4217.py")
iso = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iso)


def entry(code="EUR", num="978", mnr="2", country="A", name="Euro", extra=""):
    return (f"<CcyNtry><CtryNm>{country}</CtryNm><CcyNm>{name}</CcyNm><Ccy>{code}</Ccy>"
            f"<CcyNbr>{num}</CcyNbr><CcyMnrUnts>{mnr}</CcyMnrUnts>{extra}</CcyNtry>")


def doc(*entries):
    return f"<ISO_4217><CcyTbl>{''.join(entries)}</CcyTbl></ISO_4217>".encode()


def test_fixture_gives_exact_rows_sorted_and_deduplicated():
    r = iso.parse((HERE / "fixtures" / "iso4217-synthetic.xml").read_bytes())
    assert [x["code"] for x in r] == ["AFN", "ALL", "CLF", "EUR", "JPY", "THB", "USD", "XDR"]  # EUR once, no-currency skipped
    by = {x["code"]: x for x in r}
    assert by["EUR"] == {"code": "EUR", "numeric_code": "978", "name": "Euro", "minor_units": 2, "is_fund": False}
    assert by["ALL"]["numeric_code"] == "008"  # leading zeros kept, a string
    assert by["JPY"]["minor_units"] == 0
    assert by["XDR"]["minor_units"] is None  # "N.A."
    assert by["CLF"]["is_fund"] is True and by["CLF"]["minor_units"] == 4


def test_order_of_countries_does_not_change_the_result():
    a, b = entry(country="A", name="Euro"), entry(country="B", name="Euro Two")
    assert iso.parse(doc(a, b)) == iso.parse(doc(b, a))


@pytest.mark.parametrize("raw", [
    b"",
    b"<ISO_4217><CcyTbl>",
    b"<other/>",
    b'<!DOCTYPE x [<!ENTITY a "b">]>' + doc(entry()),
    doc(),
    doc("<CtryNm>X</CtryNm>"),  # not a CcyNtry
    doc(entry(extra="<Surprise/>")),
    doc(entry(code="eur")),
    doc(entry(num="98")),
    doc(entry(mnr="two")),
    doc(entry(), entry(num="979", country="B")),  # same code, different numeric
])
def test_bad_input_raises(raw):
    with pytest.raises((ValueError, SyntaxError)):  # SyntaxError is ElementTree.ParseError
        iso.parse(raw)


MOD, GOOD = iso, doc(entry())
PLANTED = '<!DOCTYPE ISO_4217 [<!ENTITY a "PlantedA">]>' + doc(entry(name="&a;&a;&a;&a;")).decode()


ENTITY_DOC = '<!DOCTYPE {root} [<!ENTITY a "Planted">]>'


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32"])
def test_a_document_that_is_not_utf8_is_refused_before_expat_can_expand_it(encoding):
    """F6: the old grep read the raw bytes, so UTF-16 passed it and expat expanded the entity."""
    with pytest.raises(ValueError):
        MOD.parse(PLANTED.encode(encoding))


def test_a_declared_encoding_other_than_utf8_is_refused():
    for enc in ("ISO-8859-1", "UTF-16", "windows-1252"):
        with pytest.raises(ValueError, match="encoding"):
            MOD.parse(f'<?xml version="1.0" encoding="{enc}"?>'.encode() + GOOD)
    for enc in ("UTF-8", "utf-8", "US-ASCII"):
        assert MOD.parse(f'<?xml version="1.0" encoding="{enc}"?>'.encode() + GOOD) == MOD.parse(GOOD)


def test_a_utf8_bom_is_accepted():
    assert MOD.parse(b"\xef\xbb\xbf" + GOOD) == MOD.parse(GOOD)


@pytest.mark.parametrize("prefix", ["<!DOCTYPE x>", ENTITY_DOC, "<!doctype x>"])
def test_expat_itself_refuses_any_doctype_or_entity_declaration(prefix):
    with pytest.raises((ValueError, SyntaxError)):
        MOD.parse(prefix.format(root="x").encode() + GOOD)
    with pytest.raises(ValueError, match="DOCTYPE or ENTITY"):
        MOD.parse(ENTITY_DOC.format(root="x").encode() + GOOD)
