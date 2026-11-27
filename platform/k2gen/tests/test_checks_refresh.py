"""k2gen.checks.refresh (W1-6): the good set with a refresh overlay passes every check, each failing
case fails its product with exactly its code, every code has a case, and the repository's
kraken-trades compiles its transitive refresh target."""

from collections.abc import Callable
from typing import Any

import pytest
import yaml
from test_model import docset, good
from test_repository_documents import repository_set

from k2gen import checks, model
from k2gen.checks import refresh
from k2gen.emit import pipeline
from k2gen.model import Finding

LOAD, READINGS = "contracts/ops-insight/daily-load", "contracts/sensor-net/station-readings"
LOAD_ID, READINGS_ID = "ops-insight.daily-load", "sensor-net.station-readings"
WARN = {"name": "rows_present", "type": "custom", "engine": "dbt", "description": "Warns when empty.",
        "implementation": {"k2_quality": {"arguments": {"query": "select count(*) from ${table}", "holds": "metric_value > 0"},
                                          "config": {"severity": "warn"}}}}


def edited(documents: dict[str, bytes], path: str, change: Callable[[Any], None]) -> dict[str, bytes]:
    doc = model.parse(path, documents[path])
    change(doc)
    documents[path] = yaml.safe_dump(doc, sort_keys=False).encode()
    return documents


def prop(name: str, value: Any) -> Callable[[Any], None]:
    """Set (or, with None, drop) one product property."""
    def change(doc: Any) -> None:
        doc["customProperties"] = [p for p in doc.get("customProperties", []) if p["property"] != name]
        if value is not None:
            doc["customProperties"].append({"property": name, "value": value})
    return change


def warn(doc: Any) -> None:
    doc["schema"][0].setdefault("quality", []).append(WARN)


def overlay() -> dict[str, bytes]:
    """daily-load, which reads station-readings, refreshes it, and can be degraded."""
    documents = edited(good(), f"{LOAD}/product.odps.yaml", prop("refresh", READINGS_ID))
    return edited(documents, f"{LOAD}/gold_daily_load.odcs.yaml", warn)


def _retired(doc: Any) -> None:
    doc["status"] = "retired"
    prop("lifecycleChange", {"since": "2026-11-01", "reason": "Replaced.", "decision": "ADR-056"})(doc)


def _downstream() -> dict[str, bytes]:
    """station-readings, which daily-load reads, refreshing daily-load."""
    documents = edited(good(), f"{READINGS}/product.odps.yaml", prop("refresh", LOAD_ID))
    return edited(documents, f"{READINGS}/silver_readings.odcs.yaml", warn)


def _chained() -> dict[str, bytes]:
    return edited(overlay(), f"{READINGS}/product.odps.yaml", prop("refresh", LOAD_ID))


CASES = {  # name: (code, the product file whose findings are exactly it, documents)
    "an unknown target": ("refresh.target-unresolved", LOAD, lambda: edited(
        overlay(), f"{LOAD}/product.odps.yaml", prop("refresh", "sensor-net.nowhere"))),
    "a retired target": ("refresh.target-unresolved", LOAD, lambda: edited(
        overlay(), f"{READINGS}/product.odps.yaml", _retired)),
    "itself": ("refresh.target-self", LOAD, lambda: edited(
        overlay(), f"{LOAD}/product.odps.yaml", prop("refresh", LOAD_ID))),
    "a target with no schedule": ("refresh.target-unscheduled", LOAD, lambda: edited(
        overlay(), f"{READINGS}/product.odps.yaml", prop("schedule", None))),
    "a downstream target": ("refresh.target-not-upstream", READINGS, _downstream),
    "a target with its own refresh": ("refresh.target-chains", LOAD, _chained),
    "a product with no warn rule": ("refresh.never-degraded", LOAD, lambda: edited(
        good(), f"{LOAD}/product.odps.yaml", prop("refresh", READINGS_ID))),
}


def run(documents: dict[str, bytes]) -> list[Finding]:
    return checks.run(docset(documents), None)


def test_the_overlay_has_no_finding_and_compiles_its_refresh() -> None:
    documents = overlay()
    assert run(documents) == []
    m = model.load(docset(documents))
    (p,) = [p for p in m.products if p.id == LOAD_ID]
    assert pipeline.product_pipeline(m, p)["refresh"] == READINGS_ID


@pytest.mark.parametrize("name", CASES)
def test_each_failing_case_yields_exactly_its_code(name: str) -> None:
    code, product, documents = CASES[name]
    found = [f for f in run(documents()) if f.path == f"{product}/product.odps.yaml"]
    assert {f.code for f in found} == {code}, found
    assert all(f.message.startswith("$.customProperties[") for f in found), found


def test_every_code_has_a_failing_case() -> None:
    assert {code for code, _, _ in CASES.values()} == set(refresh.CODES)


def test_kraken_trades_refreshes_instruments_through_the_security_master() -> None:
    """Transitive: kraken-trades reads the master, which reads kraken-instruments."""
    docs = repository_set()
    assert refresh.check(docs, None) == []
    m = model.load(docs)
    (trades,) = [p for p in m.products if p.id == "market-capture.kraken-trades"]
    assert "market-capture.kraken-instruments" not in {port.contract_id.rsplit(".", 1)[0] for port in trades.input_ports}
    assert pipeline.product_pipeline(m, trades)["refresh"] == "market-capture.kraken-instruments"
