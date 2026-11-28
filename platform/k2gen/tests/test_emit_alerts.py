"""emit/alerts.py (I-5, W1-17): the per-product alert rules, for the good fixture set and the
repository's products.

Goldens: `golden/alerts/<release path>` for the good set; `golden/alerts/repository/<release path>`
for the repository's products, compared group by group while each product exists. Regenerate
both, then review the diff as code:

    cd platform/k2gen && uv run --locked python tests/test_emit_alerts.py [<repository root>]

promtool is never run here (it is a container): tests/promtool/product-alerts_test.yml is its unit
test over a compiled release's rules, with the commands in its header.
"""

import dataclasses
import re
import sys
from pathlib import Path

import pytest
import yaml
from test_emit_pipeline import DAILY_ID, STATION_ID, good, loaded, replaced, repository

from k2gen import model
from k2gen.emit import alerts

GOLDEN = Path(__file__).parent / "golden" / "alerts"
REPOSITORY = GOLDEN / "repository"
MAPPING = model.REPO / "docker" / "airflow" / "statsd-mapping.yml"


def groups(text: str) -> dict[str, list[dict[str, object]]]:
    return {g["name"]: g["rules"] for g in yaml.safe_load(text)["groups"]}


def test_the_good_set_compiles_to_its_golden() -> None:
    assert alerts.emit(loaded(good())) == {alerts.PATH: (GOLDEN / alerts.PATH).read_text()}


def test_each_repository_product_compiles_to_its_golden() -> None:
    emitted = groups(alerts.emit(loaded(repository()))[alerts.PATH])
    kept = groups((REPOSITORY / alerts.PATH).read_text())
    assert emitted.keys() <= kept.keys(), "a new scheduled product: regenerate its golden and review it"
    assert emitted == {name: kept[name] for name in emitted}


@pytest.mark.parametrize("documents", [good(), repository()], ids=["good", "repository"])
def test_each_scheduled_product_gets_exactly_its_three_rules(documents: dict[str, bytes]) -> None:
    m = loaded(documents)
    found = groups(alerts.emit(m)[alerts.PATH])
    scheduled = {p.id: p for p in m.products if p.schedule is not None}
    assert found.keys() == {f"product:{pid}" for pid in scheduled}
    for pid, p in scheduled.items():
        dag = model.dag_id(p.name)
        rules = found[f"product:{pid}"]
        assert [r["alert"] for r in rules] == [f"ProductRunFailed_{dag}", f"ProductStale_{dag}", f"ProductRunSlow_{dag}"]
        for r in rules:
            labels = r["labels"]
            assert isinstance(labels, dict)
            assert {k: labels[k] for k in ("product", "dag_id", "domain")} == {"product": pid, "dag_id": dag, "domain": p.domain}
            assert labels["severity"] in ("warning", "critical")
            assert r["annotations"]["runbook"] == alerts.RUNBOOK  # type: ignore[index]
            assert f'dag_id="{dag}"' in str(r["expr"])


def test_an_unscheduled_product_gets_no_rule() -> None:
    m = replaced(loaded(good()), DAILY_ID, schedule=None)
    assert groups(alerts.emit(m)[alerts.PATH]).keys() == {f"product:{STATION_ID}"}
    nothing = dataclasses.replace(m, products=tuple(p for p in m.products if p.id == DAILY_ID))
    assert yaml.safe_load(alerts.emit(nothing)[alerts.PATH]) == {"groups": []}


@pytest.mark.parametrize(("cron", "minutes"), [
    ("*/5 * * * *", 5), ("2-59/5 * * * *", 5), ("17 * * * *", 60), ("0 4 * * *", 1440),
    ("30 15 * * 1-5", 3 * 1440),  # Friday 15:30 to Monday 15:30
    ("0 5 * * 1", 7 * 1440), ("0 9,17 * * *", 16 * 60), ("0 0 * * 7", 7 * 1440),
])
def test_the_interval_is_the_largest_gap_in_a_week(cron: str, minutes: int) -> None:
    assert alerts.interval_minutes(cron) == minutes


@pytest.mark.parametrize("cron", ["0 0 1 * *", "0 0 * 1 *", "0 0 * * MON"])
def test_a_cron_a_week_cannot_read_is_refused(cron: str) -> None:
    with pytest.raises(ValueError):
        alerts.interval_minutes(cron)


def test_windows_thresholds_and_severity_follow_the_contract() -> None:
    m = loaded(good())
    station = next(p for p in m.products if p.id == STATION_ID)  # 15 * * * *, timeout 10, bronze and silver
    daily = next(p for p in m.products if p.id == DAILY_ID)  # 30 1 * * *, no timeout, gold
    assert (alerts.stale_minutes(station), alerts.severity(station)) == (180, "warning")
    assert (alerts.stale_minutes(daily), alerts.severity(daily)) == (3 * 1440, "critical")
    rules = groups(alerts.emit(m)[alerts.PATH])
    assert "[3h]" in str(rules[f"product:{STATION_ID}"][1]["expr"]) and "> 480" in str(rules[f"product:{STATION_ID}"][2]["expr"])
    assert "> 69120" in str(rules[f"product:{DAILY_ID}"][2]["expr"])  # 80% of the 1440-minute interval
    # a run may take its whole timeout: the stale window is never under one interval plus it
    slow = dataclasses.replace(station, schedule=model.Schedule("*/5 * * * *", 40))
    assert alerts.stale_minutes(slow) == 45


def test_the_rule_file_is_yaml_with_unique_alert_names() -> None:
    names = [r["alert"] for rules in groups(alerts.emit(loaded(repository()))[alerts.PATH]).values() for r in rules]
    assert names and len(names) == len(set(names))
    assert all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(n)) for n in names)  # a legacy metric name, as rulefmt reads one


def _exporter_name(statsd: str, mappings: list[dict[str, object]]) -> str:
    """The exporter's metric for a statsd name: the first mapping whose glob matches (`*` is one
    dot-separated part) names it, else the default, dots to underscores."""
    for m in mappings:
        if re.fullmatch(re.escape(str(m["match"])).replace(r"\*", "[^.]+"), statsd):
            return str(m["name"])
    return statsd.replace(".", "_")


def test_expressions_read_only_names_the_exporter_serves() -> None:
    doc = yaml.safe_load(MAPPING.read_text())
    mappings = doc.get("mappings") or []
    assert all(m.get("match_type", "glob") == "glob" for m in mappings), "a regex mapping: read it here"
    assert (doc.get("defaults") or {}).get("observer_type", "summary") == "summary"  # a timer's _sum and _count
    served: set[str] = set()
    for statsd, name in alerts.STATSD.items():
        assert _exporter_name(statsd, mappings) == name, f"{MAPPING.name} serves {statsd} under another name"
        served |= {name} if statsd == "airflow.ti.running" else {f"{name}_sum", f"{name}_count"}
    text = alerts.emit(loaded(repository()))[alerts.PATH]
    exprs = " ".join(str(r["expr"]) for rules in groups(text).values() for r in rules)
    names = set(re.findall(r"\b([a-z_][a-z0-9_]*)\s*\{", exprs))
    assert names and names <= served, names - served


def regenerate(root: Path) -> None:
    for out, documents in ((GOLDEN, good()), (REPOSITORY, repository(root))):
        (out / alerts.PATH).parent.mkdir(parents=True, exist_ok=True)
        (out / alerts.PATH).write_text(alerts.emit(loaded(documents))[alerts.PATH])


if __name__ == "__main__":
    regenerate(Path(sys.argv[1]) if len(sys.argv) > 1 else model.REPO)
