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
