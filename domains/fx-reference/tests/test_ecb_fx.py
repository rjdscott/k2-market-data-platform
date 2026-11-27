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
    assert r[0] == {"rate_date": "2026-03-04", "currency": "USD", "rate": "1.1000"}
    assert r[-1] == {"rate_date": "2026-03-02", "currency": "AUD", "rate": "6.5000"}
    assert all(isinstance(x["rate"], str) for x in r)
    assert {x["currency"] for x in r} == {"USD", "JPY", "GBP", "CHF", "AUD"}
    assert {x["rate_date"] for x in r} == {"2026-03-04", "2026-03-03", "2026-03-02"}


def test_corrupt_fixture_parses_so_the_audits_can_catch_it():
    r = rows("ecb-90d-synthetic-corrupt.xml")
    assert {"rate_date": "2026-03-04", "currency": "GBP", "rate": "-3.3000"} in r  # rate_positive fails
    assert any(x["rate_date"] == "2999-12-31" for x in r)  # no_future_date fails


@pytest.mark.parametrize("raw", [
    (FIX / "ecb-90d-synthetic-truncated.xml").read_bytes(),
    b"",
    b"<other/>",
    b'<!DOCTYPE x [<!ENTITY a "b">]>' + doc(("2026-03-02", [("USD", "1.1")])),
    doc(("2026-03-02", [("USD", "1.1")]), extra="<!ENTITY a 'b'>"),
    doc(("2026-03-02", [("USD", "1.1"), ("USD", "1.2")])),  # duplicate
    doc(("2026-03-02", [("USD", "1.1")]), ("2026-03-02", [("USD", "1.1")])),
    doc(("2026-03-02", [("usd", "1.1")])),
    doc(("2026-03-02", [("USDX", "1.1")])),
    doc(("2026-03-02", [("USD", "1e3")])),
    doc(("2026-03-02", [("USD", "1,1")])),
    doc(("2026-03-02", [("USD", "NaN")])),
    doc(("2026-13-02", [("USD", "1.1")])),
    doc(),  # zero rows
    doc(("2026-03-02", [])),
    doc(("2026-03-02", [("USD", "1.1")])).replace(b' rate="1.1"', b""),  # missing attribute
])
def test_bad_input_raises(raw):
    with pytest.raises((ValueError, SyntaxError)):  # SyntaxError is ElementTree.ParseError
        ecb.parse(raw)


MOD, GOOD = ecb, doc(("2026-03-02", [("USD", "1.1")]))
# the entity expands to a valid code, so only the refusal can stop the row
PLANTED = ('<!DOCTYPE gesmes:Envelope [<!ENTITY a "USD">]>' + GOOD.decode()).replace('"USD"', '"&a;"')


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


def test_the_good_fixture_passes_both_audits_on_the_real_clock():
    """Its days are past TARGET days, so a run on any day after them lands it (scripts/verify/run-path.sh)."""
    r = rows("ecb-90d-synthetic.xml")
    assert ecb.rate_positive(r) == []
    assert ecb.no_future_date(r) == []


def test_the_corrupt_fixture_fails_both_audits_on_the_real_clock():
    r = rows("ecb-90d-synthetic-corrupt.xml")
    assert ecb.rate_positive(r) == ["2026-03-04 GBP: rate -3.3000 is not above zero"]
    future = ecb.no_future_date(r)  # its other days are past: only the far-future day fails
    assert len(future) == 5 and all(f.startswith("2999-12-31 ") for f in future)  # one per currency that day


def test_no_future_date_allows_today_and_refuses_tomorrow(monkeypatch):
    monkeypatch.setattr(ecb, "today", lambda: ecb.datetime.date(2026, 3, 3))  # a fixed clock: the boundary itself
    assert ecb.no_future_date([{"rate_date": "2026-03-03", "currency": "USD", "rate": "1.1"}]) == []
    assert len(ecb.no_future_date([{"rate_date": "2026-03-04", "currency": "USD", "rate": "1.1"}])) == 1


def test_rate_positive_refuses_zero():
    assert len(ecb.rate_positive([{"rate_date": "2026-03-02", "currency": "USD", "rate": "0.0000"}])) == 1
