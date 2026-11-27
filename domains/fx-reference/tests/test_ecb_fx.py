import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
FIX = HERE / "fixtures"
spec = importlib.util.spec_from_file_location("k2_ecb_fx", HERE.parent / "parsers" / "ecb_fx.py")
ecb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ecb)


def rows(name):
    return ecb.parse((FIX / name).read_bytes())


def doc(*days, extra=""):
    def day(t, cs):
        rates = "".join(f'<Cube currency="{c}" rate="{r}"/>' for c, r in cs)
        return f'<Cube time="{t}">{rates}</Cube>'

    body = "".join(day(t, cs) for t, cs in days)
    return ('<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
            'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">' + extra + f"<Cube>{body}</Cube></gesmes:Envelope>").encode()


def test_good_fixture_gives_exact_rows_with_string_rates():
    r = rows("ecb-90d-synthetic.xml")
    assert len(r) == 15
    assert r[0] == {"rate_date": "2030-01-03", "currency": "USD", "rate": "1.1000"}
    assert r[-1] == {"rate_date": "2030-01-01", "currency": "AUD", "rate": "6.5000"}
    assert all(isinstance(x["rate"], str) for x in r)
    assert {x["currency"] for x in r} == {"USD", "JPY", "GBP", "CHF", "AUD"}
    assert {x["rate_date"] for x in r} == {"2030-01-03", "2030-01-02", "2030-01-01"}


def test_corrupt_fixture_parses_so_the_audits_can_catch_it():
    r = rows("ecb-90d-synthetic-corrupt.xml")
    assert {"rate_date": "2030-01-03", "currency": "GBP", "rate": "-3.3000"} in r  # rate_positive fails
    assert any(x["rate_date"] == "2999-12-31" for x in r)  # no_future_date fails


@pytest.mark.parametrize("raw", [
    (FIX / "ecb-90d-synthetic-truncated.xml").read_bytes(),
    b"",
    b"<other/>",
    b'<!DOCTYPE x [<!ENTITY a "b">]>' + doc(("2030-01-01", [("USD", "1.1")])),
    doc(("2030-01-01", [("USD", "1.1")]), extra="<!ENTITY a 'b'>"),
    doc(("2030-01-01", [("USD", "1.1"), ("USD", "1.2")])),  # duplicate
    doc(("2030-01-01", [("USD", "1.1")]), ("2030-01-01", [("USD", "1.1")])),
    doc(("2030-01-01", [("usd", "1.1")])),
    doc(("2030-01-01", [("USDX", "1.1")])),
    doc(("2030-01-01", [("USD", "1e3")])),
    doc(("2030-01-01", [("USD", "1,1")])),
    doc(("2030-01-01", [("USD", "NaN")])),
    doc(("2030-13-01", [("USD", "1.1")])),
    doc(),  # zero rows
    doc(("2030-01-01", [])),
    doc(("2030-01-01", [("USD", "1.1")])).replace(b' rate="1.1"', b""),  # missing attribute
])
def test_bad_input_raises(raw):
    with pytest.raises((ValueError, SyntaxError)):  # SyntaxError is ElementTree.ParseError
        ecb.parse(raw)
