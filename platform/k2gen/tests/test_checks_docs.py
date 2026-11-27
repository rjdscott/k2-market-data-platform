"""k2gen.checks.docs (S05): the good set passes, each failing fixture fails with exactly its code,
and every code the check can emit has a failing fixture (tests/fixtures/v2/bad/docs/cases.yaml)."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from k2gen import model
from k2gen.checks import docs
from k2gen.model import DocumentSet, Finding, Provenance

FIXTURES = Path(__file__).parent / "fixtures" / "v2"
GOOD = FIXTURES / "good"
CASES = model.parse("cases.yaml", (FIXTURES / "bad" / "docs" / "cases.yaml").read_bytes())
PROVENANCE = Provenance(source="git", tag="v0.0.0-fixture", commit="0" * 40, tagger="fixture",
                        tagged_at="2026-11-27T00:00:00Z", set_sha256="0" * 64, documents=())


def good() -> dict[str, bytes]:
    return {p.relative_to(GOOD).as_posix(): p.read_bytes() for p in GOOD.rglob("*") if p.is_file()}


def changed(changes: list[dict[str, Any]]) -> dict[str, bytes]:
    """The good set with each change of a case applied, as cases.yaml's header defines them."""
    documents = good()
    for change in changes:
        file = change["file"]
        if "copy" in change:
            source = change["copy"]
            for path in [p for p in documents if p == source or p.startswith(source + "/")]:
                documents[file + path[len(source):]] = documents[path]
        elif "delete" in change:
            del documents[file]
        elif "text" in change:
            documents[file] = change["text"].encode()
        else:
            doc = model.parse(file, documents[file])
            *parents, last = (change.get("remove") or change["set"]).split("/")
            node = doc
            for key in parents:
                node = node[int(key)] if isinstance(node, list) else node[key]
            index: str | int = int(last) if isinstance(node, list) else last
            if "remove" in change:
                del node[index]
            elif index == len(node) and isinstance(node, list):
                node.append(copy.deepcopy(change["value"]))
            else:
                node[index] = copy.deepcopy(change["value"])
            documents[file] = yaml.safe_dump(doc, sort_keys=False).encode()
    return documents


def run(documents: dict[str, bytes]) -> list[Finding]:
    return docs.check(DocumentSet(documents, PROVENANCE), None)


def test_the_good_set_has_no_finding() -> None:
    assert run(good()) == []


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["code"])
def test_each_failing_fixture_yields_exactly_its_code(case: dict[str, Any]) -> None:
    found = run(changed(case["changes"]))
    assert {f.code for f in found} == {case["code"], *case.get("also", [])}, found
    assert {f.severity for f in found} == {"error"}
    assert all(f.message.startswith("$") or f.code == "docs.schema-invalid" for f in found), found  # a pointer first


@pytest.mark.parametrize("case", CASES["valid"], ids=lambda c: c["why"])
def test_each_valid_variant_has_no_finding(case: dict[str, Any]) -> None:
    assert run(changed(case["changes"])) == []


def test_every_code_has_a_failing_fixture() -> None:
    assert sorted({c["code"] for c in CASES["cases"]}) == sorted(docs.CODES)
    assert all(c.startswith("docs.") for c in docs.CODES)


def test_the_unique_key_finding_says_why() -> None:
    (case,) = [c for c in CASES["cases"] if c["code"] == "docs.incremental-key-misses-partition"][:1]
    (finding,) = run(changed(case["changes"]))
    assert finding.path.endswith("silver_readings.odcs.yaml") and "read_at" in finding.message
    assert "experiment (a) of docs/research/2026-11-26-release-spine-spikes.md" in finding.message


def test_a_schema_error_stops_the_rules() -> None:
    # bronze without its lineage column would fail a rule; a schema error elsewhere stops first
    case = [c for c in CASES["cases"] if c["code"] == "docs.lineage-column-missing"][0]
    documents = changed([*case["changes"], {"file": "contracts/ops-insight/daily-load/product.odps.yaml", "remove": "status"}])
    assert {f.code for f in run(documents)} == {"docs.schema-invalid"}


@pytest.mark.parametrize("word", sorted(model.TRINO_RESERVED))
def test_a_domain_named_any_reserved_word_is_refused(word: str) -> None:
    # model.RESERVED_DOMAIN_NAMES repeats none of TRINO_RESERVED: this check refuses each one, as a
    # domain whose fence (its catalog) is the word: current-user is the catalog current_user
    change = {"file": "contracts/domains.yaml", "set": f"domains/{word.replace('_', '-')}",
              "value": {"owner": "platform-team", "kind": "consumer-aligned", "description": "Named as a reserved word."}}
    assert {f.code for f in run(changed([change]))} == {"docs.name-reserved-word"}, word


def test_every_threshold_form_maps_to_one_condition() -> None:
    from k2gen.emit.dbt import _holds

    assert _holds({"mustBe": 0, "mustNotBe": 1.5, "mustBeGreaterThan": -1, "mustBeGreaterOrEqualTo": 1e-05, "mustBeLessThan": 9,
                   "mustBeLessOrEqualTo": 8, "mustBeBetween": [1, 2], "mustNotBeBetween": [3, 4.5]}) == (
        "metric_value = 0 and metric_value <> 1.5 and metric_value > -1 and metric_value >= 1E-05 and metric_value < 9 and "
        "metric_value <= 8 and metric_value between 1 and 2 and metric_value not between 3 and 4.5")
    for bad in ({}, {"mustBe": "many"}, {"mustBe": True}, {"mustBe": float("inf")}, {"mustBeBetween": [2, 1]}, {"mustBeBetween": [1]}):
        with pytest.raises(ValueError):
            _holds(bad)
