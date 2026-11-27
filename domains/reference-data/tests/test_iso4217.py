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
