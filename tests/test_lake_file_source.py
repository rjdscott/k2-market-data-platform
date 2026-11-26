"""docker/lake/file_source.py and docker/lake/sources/ (ADR-036), with no Spark and no network.

The source is the synthetic one: tests/synthetic_file_sources.json is what k2gen generates
for platform/k2gen/tests/synthetic/ (held there by platform/k2gen/tests/test_file_source.py).
Its files are invented here, inline. The Spark half, the branch write and the fast-forward,
is proven on the live stack in a scratch namespace (docs/runbooks/file-source-audit-failed.md).
"""

import email.message
import hashlib
import io
import json
import socket
import urllib.error
import urllib.request
import urllib.response
from datetime import date, datetime, timezone
from pathlib import Path

import file_source as F
import pytest
import sources

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "tests/synthetic_file_sources.json").read_text())["bronze.synthetic_rates"]
SHA_A, SHA_B = "a" * 64, "b" * 64
NOON = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)


def xml(*days):
    """A synthetic file: days of (date, [(code, value), ...])."""
    body = "".join(f'<day date="{d}">' + "".join(f'<rate code="{c}" value="{v}"/>' for c, v in rates) + "</day>"
                   for d, rates in days)
    return f"<rates>{body}</rates>".encode()


GOOD = xml(("2026-01-05", [("AAA", "1.2300"), ("BBB", "40.5")]), ("2026-01-06", [("AAA", "1.2400")]))


def rows_of(data, sha):
    rows, why = F.parse(SPEC, data)
    assert why is None, why
    return [{**r, F.LINEAGE: sha} for r in rows]


class Store:
    """The landing bucket, in memory, with the conditional PUT's semantics."""

    bucket = "test-landing"

    def __init__(self):
        self.objects = {}

    def put_if_absent(self, key, data, meta):
        if key in self.objects:
            return False
        self.objects[key] = (data, dict(meta))
        return True

    def get(self, key):
        return self.objects[key]


def serving(data):
    def opener(request, timeout):
        assert request.full_url == SPEC["url"] and timeout == 60
        return io.BytesIO(data)

    return opener


# What each test host resolves to; a source's host must resolve to global addresses only.
HOSTS = {"example.invalid": ["93.184.215.14"], "mirror.invalid": ["1.1.1.1"], "minio": ["172.18.0.5"],
         "localhost": ["127.0.0.1"], "metadata.invalid": ["169.254.169.254"], "split.invalid": ["1.1.1.1", "10.0.0.7"]}


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """No network: every host resolves from HOSTS."""
    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (a, port)) for a in HOSTS[host]]

    monkeypatch.setattr(F.socket, "getaddrinfo", resolve)


def web(monkeypatch, pages):
    """Answer the default opener's http and https requests from `pages`, url -> (status,
    body for a 200 or Location for a redirect), with no network; the urls asked, in order."""
    asked = []

    def answer(handler, request):
        asked.append(request.full_url)
        status, value = pages[request.full_url]
        headers = email.message.Message()
        if status != 200:
            headers["Location"] = value
        response = urllib.response.addinfourl(io.BytesIO(value if status == 200 else b""), headers,
                                              request.full_url, status)
        response.msg = "test"
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", answer)
    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", answer)
    return asked


# ── fetch ─────────────────────────────────────────────────────────────────


def test_a_redirect_to_https_on_a_global_address_is_followed(monkeypatch):
    moved = "https://mirror.invalid/rates.xml"
    asked = web(monkeypatch, {SPEC["url"]: (302, moved), moved: (200, GOOD)})
    store = Store()
    key = F.fetch(SPEC, store, NOON)
    assert asked == [SPEC["url"], moved] and store.objects[key][0] == GOOD


@pytest.mark.parametrize("target, why", [
    ("http://mirror.invalid/rates.xml", "redirect to http://mirror.invalid: not https"),
    ("https://minio/k2-landing/x?X-Amz-Signature=secret", "redirect to https://minio: it resolves to non-global 172.18.0.5"),
    ("https://user:secret@localhost:8443/x?token=secret", "redirect to https://localhost: it resolves to non-global 127.0.0.1"),
    ("https://metadata.invalid/latest", "redirect to https://metadata.invalid: it resolves to non-global 169.254.169.254"),
])
def test_a_redirect_to_plain_http_or_a_non_global_address_is_refused(monkeypatch, target, why):
    asked = web(monkeypatch, {SPEC["url"]: (302, target), target: (200, GOOD)})
    store = Store()
    with pytest.raises(SystemExit) as refused:
        F.fetch(SPEC, store, NOON)
    assert str(refused.value) == f"fetch refused {why}"  # scheme and host only: no credentials, no query
    assert asked == [SPEC["url"]] and store.objects == {}


def test_a_redirect_loop_stops_at_the_cap(monkeypatch):
    a, b = SPEC["url"], "https://mirror.invalid/rates.xml"
    asked = web(monkeypatch, {a: (302, b), b: (302, a)})
    store = Store()
    with pytest.raises(SystemExit, match=f"refused redirect {F.MAX_REDIRECTS + 1} to https://example.invalid: more than 5"):
        F.fetch(SPEC, store, NOON)
    assert len(asked) == 1 + F.MAX_REDIRECTS and store.objects == {}


@pytest.mark.parametrize("url, why", [
    ("http://example.invalid/rates.xml", "http://example.invalid: not https"),
    ("https://minio/k2-landing/x", "https://minio: it resolves to non-global 172.18.0.5"),
    ("https://split.invalid/rates.xml", "https://split.invalid: it resolves to non-global 10.0.0.7"),
])
def test_a_source_url_on_plain_http_or_a_non_global_address_is_refused(monkeypatch, url, why):
    asked = web(monkeypatch, {url: (200, GOOD)})
    store = Store()
    with pytest.raises(SystemExit) as refused:
        F.fetch({**SPEC, "url": url}, store, NOON)
    assert str(refused.value) == f"fetch refused source URL {why}"
    assert asked == [] and store.objects == {}



def test_fetch_lands_the_bytes_under_domain_product_day_and_sha256():
    store = Store()
    key = F.fetch(SPEC, store, NOON, serving(GOOD))
    assert key == f"fx-reference/synthetic-rates/2026-01-05/{hashlib.sha256(GOOD).hexdigest()}.xml"
    data, meta = store.objects[key]
    assert data == GOOD
    assert meta == {"source-url": SPEC["url"], "fetched-at": "2026-01-05T12:00:00+00:00"}


def test_a_refetch_of_unchanged_bytes_the_same_day_writes_nothing(capsys):
    store = Store()
    first = F.fetch(SPEC, store, NOON, serving(GOOD))
    later = F.fetch(SPEC, store, NOON.replace(hour=18), serving(GOOD))
    assert later == first and len(store.objects) == 1
    assert store.objects[first][1]["fetched-at"] == "2026-01-05T12:00:00+00:00"  # the first landing stands
    assert "already landed, nothing written" in capsys.readouterr().out


def test_new_bytes_or_a_new_day_are_a_new_object_and_nothing_is_overwritten():
    store = Store()
    first = F.fetch(SPEC, store, NOON, serving(GOOD))
    changed = F.fetch(SPEC, store, NOON, serving(GOOD + b"\n"))
    next_day = F.fetch(SPEC, store, NOON.replace(day=6), serving(GOOD))
    assert len({first, changed, next_day}) == 3 and store.objects[first][0] == GOOD


def test_a_download_over_the_cap_is_refused_and_not_landed(monkeypatch):
    monkeypatch.setattr(F, "MAX_BYTES", 10)
    store = Store()
    with pytest.raises(SystemExit, match="larger than 10 bytes"):
        F.fetch(SPEC, store, NOON, serving(b"x" * 11))
    assert store.objects == {}


def test_only_a_landed_key_of_this_source_is_accepted():
    good = f"fx-reference/synthetic-rates/2026-01-05/{SHA_A}.xml"
    assert F.object_sha(SPEC, good) == SHA_A
    for bad in (f"reference-data/synthetic-rates/2026-01-05/{SHA_A}.xml", f"fx-reference/synthetic-rates/2026-01-05/{SHA_A}.csv",
                f"fx-reference/synthetic-rates/../x/2026-01-05/{SHA_A}.xml", "fx-reference/synthetic-rates/2026-01-05/abc.xml", "None"):
        with pytest.raises(SystemExit, match="is not a landed file"):
            F.object_sha(SPEC, bad)


def test_a_landed_file_whose_bytes_changed_is_refused():
    store = Store()
    key = F.fetch(SPEC, store, NOON, serving(GOOD))
    assert F.read_landed(SPEC, key, store)[0] == key.split("/")[-1][:64]
    store.objects[key] = (GOOD.replace(b"1.2300", b"9.9999"), store.objects[key][1])
    with pytest.raises(SystemExit, match="changed after landing"):
        F.read_landed(SPEC, key, store)


def test_the_conditional_put_maps_412_to_already_landed_and_raises_anything_else(monkeypatch):
    monkeypatch.setenv("MINIO_ROOT_USER", "test-user")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "test-password")
    landing, sent = F.Landing("test-landing"), []

    def refuse(code):
        def call(method, key, body=b"", headers=None):
            sent.append(headers)
            raise urllib.error.HTTPError("http://minio:9000/x", code, "refused", {}, None)

        return call

    monkeypatch.setattr(landing, "_call", refuse(412))
    assert landing.put_if_absent("k", b"x", {"source-url": "u"}) is False
    assert sent[0] == {"if-none-match": "*", "x-amz-meta-source-url": "u"}
    monkeypatch.setattr(landing, "_call", refuse(403))
    with pytest.raises(urllib.error.HTTPError):
        landing.put_if_absent("k", b"x", {})


def test_sign_matches_the_aws_signature_v4_test_suite():
    # get-vanilla from the AWS Signature Version 4 test suite: its documented example
    # credentials, not a key of anything.
    headers = {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"}
    empty = hashlib.sha256(b"").hexdigest()
    auth = F.sign("GET", "/", headers, empty, "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                  "us-east-1", service="service")
    assert auth == ("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
                    "SignedHeaders=host;x-amz-date, "
                    "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


# ── the parser interface ──────────────────────────────────────────────────


def test_a_parser_is_one_function_from_bytes_to_the_tables_rows():
    rows, why = F.parse(SPEC, GOOD)
    assert why is None
    assert rows == [
        {"rate_date": date(2026, 1, 5), "code": "AAA", "value": "1.2300"},
        {"rate_date": date(2026, 1, 5), "code": "BBB", "value": "40.5"},
        {"rate_date": date(2026, 1, 6), "code": "AAA", "value": "1.2400"},
    ]


@pytest.mark.parametrize(
    "data, why",
    [
        (b"<rates><day date='2026-01-05'>", "ParseError"),
        (b"<prices/>", "root element is <prices>"),
        (b'<rates><day date="2026-01-05"><rate code="AAA"/></day></rates>', "KeyError"),
        (xml(("2026-01-05", [("AAA", "1,23")])), "value '1,23' is not a decimal"),
        (xml(("2026-01-05", [("AAA", "NaN")])), "value 'NaN' is not a decimal"),
    ],
)
def test_a_file_that_does_not_read_as_rows_fails_parses_with_why(data, why):
    rows, problem = F.parse(SPEC, data)
    assert rows is None and why in problem


def test_rows_with_other_columns_than_the_table_fail_parses(monkeypatch):
    module = sources.parser("synthetic_rates")
    monkeypatch.setattr(module, "parse", lambda data: [{"rate_date": date(2026, 1, 5), "code": "AAA"}])
    rows, why = F.parse(SPEC, GOOD)
    assert rows is None and "columns ['code', 'rate_date'], the table ['code', 'rate_date', 'value']" in why


def test_entity_expansion_is_refused_by_the_bundled_expat():
    bomb = ['<?xml version="1.0"?><!DOCTYPE rates [<!ENTITY a0 "lol">']
    bomb += [f'<!ENTITY a{i} "{f"&a{i - 1};" * 10}">' for i in range(1, 10)]
    bomb += [']><rates><day date="2026-01-05"><rate code="AAA" value="&a9;"/></day></rates>']
    rows, why = F.parse(SPEC, "".join(bomb).encode())
    assert rows is None and "amplification" in why


def test_a_parser_loads_only_after_the_expat_check():
    sources.require_expat()  # the interpreter running this suite is new enough
    with pytest.raises(RuntimeError, match="expat 2.4.0 is older than 2.4.1"):
        sources.require_expat((2, 4, 0))
    with pytest.raises(ValueError, match="not a parser module name"):
        sources.parser("../file_source")


def test_every_parser_and_product_audit_a_source_names_exists():
    module = sources.parser(SPEC["parser"])
    assert callable(module.parse)
    assert all(callable(getattr(module, a)) for a in SPEC["audits"])


def test_the_generic_checks_are_the_names_k2gen_refuses_for_a_product_audit():
    schema = json.loads((ROOT / "contracts/schema/k2.schema.json").read_text())
    refused = schema["$defs"]["customProperties"]["properties"]["fileSource"]["properties"]["audits"]["items"]["not"]["enum"]
    assert refused == list(F.GENERIC)


# ── the audits ────────────────────────────────────────────────────────────


def audit(data, branch, sha=SHA_B):
    rows, why = F.parse(SPEC, data)
    assert why is None
    module = sources.parser(SPEC["parser"])
    found = F.checks(SPEC, rows, branch, sha, [(a, getattr(module, a)) for a in SPEC["audits"]])
    assert [c[0] for c in found] == [*F.GENERIC, *SPEC["audits"]]
    return {name: (passed, observed, detail) for name, passed, observed, detail in found}


def failed(result):
    return sorted(name for name, (passed, _, _) in result.items() if not passed)


def test_a_good_file_staged_on_an_empty_table_passes_every_check():
    result = audit(GOOD, rows_of(GOOD, SHA_B))
    assert failed(result) == [] and result["parses"] == (True, 3, "")


def test_an_empty_file_fails_non_empty():
    assert failed(audit(b"<rates/>", [])) == ["non_empty"]


def test_a_key_twice_in_one_file_fails_keys_unique():
    data = xml(("2026-01-05", [("AAA", "1.23"), ("AAA", "1.23")]))
    result = audit(data, rows_of(data, SHA_B))
    assert failed(result) == ["keys_unique"]
    assert result["keys_unique"][1:] == (1, "key 2026-01-05, AAA appears 2 times")


def test_a_key_outside_its_pattern_fails_in_domain():
    data = xml(("2026-01-05", [("aaa", "1.23"), ("AAAA", "2")]))
    result = audit(data, rows_of(data, SHA_B))
    assert failed(result) == ["in_domain"] and result["in_domain"][1] == 2


def test_a_published_value_written_another_way_is_unchanged():
    # 1.2300 published by file A; file B writes 1.23, the same number: nothing to stage.
    published = rows_of(GOOD, SHA_A)
    again = GOOD.replace(b"1.2300", b"1.23").replace(b"40.5", b"40.50")
    assert F.to_stage(SPEC, rows_of(again, SHA_B), published) == []
    assert failed(audit(again, published)) == []


def test_a_published_value_changed_by_a_later_file_fails_values_unchanged():
    published = rows_of(GOOD, SHA_A)
    revised = GOOD.replace(b"1.2300", b"1.2310")
    staged = rows_of(revised, SHA_B)
    branch = published + F.to_stage(SPEC, staged, published)  # what load leaves on the branch
    result = audit(revised, branch)
    assert failed(result) == ["values_unchanged"]
    assert result["values_unchanged"][1:] == (1, "key 2026-01-05, AAA: published 1.2300, this file 1.2310")


def test_a_file_row_missing_from_the_branch_fails_staged():
    data = xml(("2026-01-05", [("AAA", "1.23"), ("BBB", "2")]))
    result = audit(data, rows_of(data, SHA_B)[:1])
    assert failed(result) == ["staged"] and result["staged"][1] == 1


def test_a_product_audit_runs_after_the_generic_ones():
    data = xml(("2026-01-05", [("AAA", "-1.23"), ("BBB", "0")]))
    result = audit(data, rows_of(data, SHA_B))
    assert failed(result) == ["positive_values"] and result["positive_values"][1] == 2


def test_a_long_list_of_problems_is_counted_not_quoted():
    data = xml(("2026-01-05", [(c * 3, "1") for c in "abcdefg"]))
    observed, detail = audit(data, rows_of(data, SHA_B))["in_domain"][1:]
    assert observed == 7 and detail.endswith("; and 2 more") and detail.count("does not match") == 5


# ── load twice, and the publish guard ─────────────────────────────────────


def test_loading_the_same_file_twice_adds_no_row_and_changes_no_value():
    rows = rows_of(GOOD, SHA_A)
    main = F.to_stage(SPEC, rows, [])  # the first load stages every row, the publish makes it main
    assert main == rows
    assert F.to_stage(SPEC, rows, main) == []  # the second stages nothing
    assert failed(audit(GOOD, main, sha=SHA_A)) == []


def test_a_later_file_stages_only_its_new_rows():
    main = rows_of(GOOD, SHA_A)
    later = xml(("2026-01-06", [("AAA", "1.24")]), ("2026-01-07", [("AAA", "1.25")]))
    new = F.to_stage(SPEC, rows_of(later, SHA_B), main)
    assert [(r["rate_date"], r["value"]) for r in new] == [(date(2026, 1, 7), "1.25")]


def test_an_audit_row_names_the_table_the_file_and_the_branch_snapshot_it_checked():
    # The publish guard selects by this scope, so an audit of an earlier branch snapshot
    # of the same file never licenses a later one.
    assert F.scope_of("bronze.synthetic_rates", SHA_A, 42) == f"bronze.synthetic_rates {SHA_A} 42"
    assert F.scope_of("bronze.synthetic_rates", SHA_A, None).endswith(" none")
    assert F.branch_of(SHA_A) == "wap_" + "a" * 16


def test_publish_needs_the_newest_audit_of_the_file_to_have_passed_every_check():
    expected = [*F.GENERIC, "positive_values"]
    t1, t2 = datetime(2026, 1, 5, 12), datetime(2026, 1, 5, 13)
    passed_at = [(c, True, t2) for c in expected]
    assert F.unaudited([], expected) == "no audit of this file at this branch snapshot is recorded"
    assert F.unaudited([(c, c != "staged", t2) for c in expected], expected).startswith(
        "its newest audit, at 2026-01-05 13:00:00, failed ['staged']")
    assert F.unaudited([(c, False, t1) for c in expected] + passed_at, expected) is None  # a re-audit that passed
    assert F.unaudited([(c, True, t1) for c in expected] + [("parses", False, t2)], expected) is not None
    assert "lacks ['positive_values']" in F.unaudited(passed_at[:-1], expected)
