"""The repository's own v2 documents load into the model (model.load, every schema applied).

test_interfaces.py and test_model.py hold the schemas and the loader to the fixture set; this
holds the real products under contracts/, and tests/access/expectations.yaml, to them.
"""

import pytest

from k2gen import model
from k2gen.model import DocumentSet


def repository_set() -> DocumentSet:
    """The working tree's files under model.DOCUMENT_ROOTS, as a tag's document set holds them."""
    documents = {}
    for root in model.DOCUMENT_ROOTS:
        base = model.REPO / root
        paths = sorted(base.rglob("*")) if root.endswith("/") else [base]
        documents |= {p.relative_to(model.REPO).as_posix(): p.read_bytes() for p in paths if p.is_file()}
    provenance = model.Provenance("git", "working-tree", "0" * 40, "k2gen tests", "1970-01-01T00:00:00Z",
                                  model.set_sha256(documents), ())
    return DocumentSet(documents, provenance)


def test_the_repository_set_loads() -> None:
    products = {p.id: p for p in model.load(repository_set()).products}
    assert {"governance.access-canary", "market-analytics.hot-gold"} <= products.keys()


def test_each_canary_policy_has_an_expectation_that_agrees_with_it() -> None:
    m = model.load(repository_set())
    (canary,) = [p for p in m.products if p.id == "governance.access-canary"]
    (events,) = canary.tables
    reads = {(e.principal, e.table): e for e in m.expectations if isinstance(e, model.TableExpectation)}
    for grant in canary.grants:
        row = reads[(grant.principal, model.reader_relation(events))]
        policy = events.policies.get(grant.role, model.RolePolicy(None, (), None))
        assert set(row.visible) == {c.name for c in events.columns}
        assert set(row.masked) == set(policy.mask)
        assert row.min_row_age_minutes == (policy.delay.minutes if policy.delay else None)
        assert row.must_not_appear  # every reader is held to the seed's ids
        assert row.must_appear and row.must_appear.min_rows >= 1  # and a canary that built no rows fails
    assert {e.table for e in m.expectations if isinstance(e, model.DeniedExpectation)} >= {
        model.input_relation(events),  # the base, behind the view
        *(model.base_relation(canary.domain, canary.name, s.removesuffix(".csv")) for s in canary.seeds)}  # the seed's table


def test_the_repository_set_passes_every_check() -> None:
    from k2gen import checks

    assert [f"{f.code} {f.path}: {f.message}" for f in checks.run(repository_set(), None)] == []


def test_the_hot_tier_is_a_paused_serving_copy_with_no_lake_table() -> None:
    (hot,) = [p for p in model.load(repository_set()).products if p.id == "market-analytics.hot-gold"]
    assert hot.status == "paused" and hot.lifecycle_change and hot.lifecycle_change.decision == "ADR-057"
    assert [port.serves for port in hot.output_ports] == [model.Serves("clickhouse", "hot-gold", "paused")]
    assert {t.build.kind for t in hot.tables} == {"serving"} and hot.grants == ()
    with pytest.raises(ValueError, match="LAKE_BUILDS"):
        model.reader_relation(hot.tables[0])


def test_a_grant_outside_the_team_needs_terms_upstream_too() -> None:
    """A3 of the 2026-11-28 review. The ECB product grants outside its team over the calendars,
    which record no terms and pass: every row of theirs was written on the platform (a seed, no
    connector, no input). The new products grant nobody, so they pass; a grant on candles outside
    its team names each venue-fed product upstream of it that records no terms, and candles."""
    import yaml

    from k2gen import checks

    documents = dict(repository_set().documents)
    assert [f for f in checks.run(DocumentSet(documents, repository_set().provenance), None) if f.code == "docs.terms-missing"] == []
    grants = "contracts/market-analytics/candles/grants.yaml"
    documents[grants] = yaml.safe_dump({"grants": [{"principal": "p.analyst-a", "role": "full_reader"}]}).encode()
    found = [f for f in checks.docs.check(DocumentSet(documents, repository_set().provenance), None) if f.code == "docs.terms-missing"]
    assert [f.path for f in found] == [grants, grants], found
    assert "record the source's terms in the product's terms property" in found[0].message
    for upstream in ("market-analytics.canonical-trades", "market-capture.kraken-trades", "reference-data.security-master",
                     "market-capture.kraken-instruments", "reference-data.currency-master"):
        assert upstream in found[1].message, upstream
    assert "calendars" not in found[1].message


def test_a_partner_is_refused_over_terms_that_forbid_redistribution_upstream_an_exempt_product_included() -> None:
    """The ECB product, allowed, passes to a partner over the calendars, which record no terms and
    are exempt. Once the calendars record forbidden, the same grant is refused and names them."""
    import yaml

    from k2gen import checks

    documents = dict(repository_set().documents)
    documents["contracts/fx-reference/ecb-fx-rates/grants.yaml"] = yaml.safe_dump(
        {"grants": [{"principal": "svc.partner-dashboard", "role": "full_reader"}]}).encode()
    assert checks.docs.check(DocumentSet(documents, repository_set().provenance), None) == []
    calendars = "contracts/reference-data/calendars/product.odps.yaml"
    doc = yaml.safe_load(documents[calendars])
    doc["customProperties"].append({"property": "terms", "value": {"redistribution": "forbidden"}})
    documents[calendars] = yaml.safe_dump(doc, sort_keys=False).encode()
    (found,) = checks.docs.check(DocumentSet(documents, repository_set().provenance), None)
    assert found.code == "docs.terms-forbid-redistribution" and "svc.partner-dashboard" in found.message
    assert "reference-data.calendars say redistribution: forbidden" in found.message


@pytest.mark.parametrize("product, upstream", [("currency-master", ()), ("security-master", ("reference-data.currency-master",))])
def test_an_outside_grant_on_the_currency_master_or_what_reads_it_is_refused(product: str, upstream: tuple[str, ...]) -> None:
    """currency and asset offer full_reader with nobody holding it. SIX's terms for the ISO 4217
    lists are not established, so the currency master records no terms property, and a grant
    outside reference-data-team on it, or on the security master that reads it, fails."""
    import yaml

    from k2gen import checks

    documents = dict(repository_set().documents)
    grants = f"contracts/reference-data/{product}/grants.yaml"
    documents[grants] = yaml.safe_dump({"grants": [{"principal": "p.analyst-a", "role": "full_reader"}]}).encode()
    found = [f for f in checks.docs.check(DocumentSet(documents, repository_set().provenance), None) if f.code == "docs.terms-missing"]
    assert found and {f.path for f in found} == {grants}, found
    assert "record the source's terms in the product's terms property" in found[0].message
    for u in upstream:
        assert u in found[-1].message, u
