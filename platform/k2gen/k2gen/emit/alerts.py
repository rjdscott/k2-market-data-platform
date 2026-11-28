"""Emits PATH, compiled/prometheus/product-alerts.yml: three alert rules for every product with a
schedule (I-5, docs/development/phase-001-interfaces.md, "Per-product alerts"), from the model
alone. Prometheus reads it through its pointer (docker/prometheus/prometheus.yml rule_files) and
rereads it on the SIGHUP deploy sends at each swap. A product with no schedule has no DAG, so no rule.

Each product's group, `product:<id>`, holds
- ProductRunFailed_<dag>: a run of its DAG ended failed in the last FAILED_WINDOW (a pulse, as
  AirflowTaskFailed is); severity by the product's layer (severity()).
- ProductStale_<dag>: no successful run within stale_minutes() (STALE_INTERVALS schedule intervals,
  at least one interval plus the run's timeout), muted while its maintain or compact task runs;
  severity by layer.
- ProductRunSlow_<dag>: the runs that succeeded within the last schedule interval took on average
  more than SLOW_FRACTION of the run's timeoutMinutes (the interval when it has none); warning.

The series, all from Airflow's statsd through the airflow-statsd exporter (docker-compose.yml):
- airflow.dagrun.duration.{success,failed}, Airflow's timer at a run's end, tags dag_id and
  run_type (dag_id alone on a run failed by its dagrun_timeout): a summary, of which only _sum and
  _count are read. Measured live in ADR-034 (`airflow_dagrun_duration_success{dag_id,run_type,
  quantile}`, in seconds); docker/grafana/dashboards/k2-airflow.json reads both _sum and _count.
- airflow.ti.running, tags queue, dag_id and task_id: the scheduler's gauge of running tries, the
  one name docker/airflow/statsd-mapping.yml maps (with a 2-minute ttl).
The other names keep the exporter's default, dots to underscores (STATSD); the test reads the
mapping file, so a mapping that renames one fails it.

Counters live in the exporter's memory: a series exists only from its first increment, and an
exporter restart drops it. So each rule also reads a series new within its window (increase()
of a lone sample is 0), as airflow-alerts.yml does, and ProductStale reads a series absent for
its whole window as stale (no success since the exporter started, or a DAG never unpaused).
"""

import json

from k2gen.model import Model, Product, dag_id

PATH = "compiled/prometheus/product-alerts.yml"
RUNBOOK = "docs/runbooks/airflow-scheduling.md"
# statsd name Airflow sends -> the exporter's metric name
STATSD = {
    "airflow.dagrun.duration.success": "airflow_dagrun_duration_success",
    "airflow.dagrun.duration.failed": "airflow_dagrun_duration_failed",
    "airflow.ti.running": "airflow_ti_running",
}
SUCCESS, FAILED, RUNNING = STATSD.values()
FAILED_WINDOW = 15  # minutes, as AirflowTaskFailed
# ponytail: one N for every product, so a weekly one waits three weeks. A per-product N (a field
# of the contract's schedule) when one product needs its staleness sooner than the others.
STALE_INTERVALS = 3
STALE_FOR = "2m"  # the gap between maintain or compact ending (unmuted) and the success that follows
SLOW_FRACTION = 0.8
MAINTAIN_TASKS = "maintain|compact"  # factory.py: compact follows maintain in a Kafka product's DAG
CRITICAL_LAYERS = {"gold", "mart"}  # what readers query; bronze and silver feed other products
WEEK = 7 * 24 * 60


def _field(spec: str, lo: int, hi: int) -> set[int]:
    """One cron field's values: `*`, `n`, `a-b`, each with an optional `/step`, comma separated."""
    out: set[int] = set()
    for part in spec.split(","):
        rng, _, step = part.partition("/")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = (int(x) for x in rng.split("-"))
        else:
            a = int(rng)
            b = hi if step else a
        out.update(range(a, b + 1, int(step or 1)))
    return out


def interval_minutes(cron: str) -> int:
    """The largest gap between two firings of a five-field cron over a week, in minutes: the
    interval of a regular schedule, the weekend of a weekday one."""
    minute, hour, dom, month, dow = cron.split()
    # ponytail: a week's firings only; a day-of-month or month field needs a year's, and no
    # product schedules by one. Expand over a year when one does.
    if (dom, month) != ("*", "*"):
        raise ValueError(f"{cron!r}: a day-of-month or month field; the alert emitter reads a week's firings only")
    days = {d % 7 for d in _field(dow, 0, 7)}  # 0 and 7 are Sunday
    times = sorted(d * 1440 + h * 60 + m for d in days for h in _field(hour, 0, 23) for m in _field(minute, 0, 59))
    if not times:
        raise ValueError(f"{cron!r}: never fires")
    return max(b - a for a, b in zip(times, [*times[1:], times[0] + WEEK], strict=True))


def stale_minutes(p: Product) -> int:
    """STALE_INTERVALS intervals, and never less than one interval plus the run's timeout: a run
    may take up to its timeout (maintain included, which runs inside it) before its success counts."""
    assert p.schedule is not None
    interval = interval_minutes(p.schedule.cron)
    return max(STALE_INTERVALS * interval, interval + (p.schedule.timeout_minutes or 0))


def severity(p: Product) -> str:
    return "critical" if {t.layer for t in p.tables} & CRITICAL_LAYERS else "warning"


def _duration(minutes: int) -> str:
    return f"{minutes // 1440}d" if minutes % 1440 == 0 else f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"


def _q(s: str) -> str:
    """A YAML double-quoted scalar (JSON's escapes are YAML's)."""
    return json.dumps(s)


def _rule(alert: str, expr: str, labels: dict[str, str], summary: str, description: str, for_: str | None = None) -> str:
    lines = [f"      - alert: {alert}", "        expr: |"]
    lines += [f"          {line}" if line else "" for line in expr.splitlines()]
    if for_:
        lines.append(f"        for: {for_}")
    lines.append("        labels:")
    lines += [f"          {k}: {_q(v)}" for k, v in labels.items()]
    lines += ["        annotations:", f"          summary: {_q(summary)}", f"          description: {_q(description)}",
              f"          runbook: {RUNBOOK}"]
    return "\n".join(lines)


def rules(p: Product) -> list[tuple[str, str]]:
    """The product's three rules, each (alert name, YAML text); `p.schedule` is set."""
    assert p.schedule is not None
    dag = dag_id(p.name)
    sel = f'{{dag_id="{dag}"}}'
    labels = {"severity": severity(p), "product": p.id, "dag_id": dag, "domain": p.domain}
    interval, stale = interval_minutes(p.schedule.cron), stale_minutes(p)
    timeout = p.schedule.timeout_minutes
    slow_s = round(SLOW_FRACTION * 60 * (timeout or interval))
    fw, sw, iw = _duration(FAILED_WINDOW), _duration(stale), _duration(interval)
    failed = (f"max by (dag_id) (\n"
              f"  increase({FAILED}_count{sel}[{fw}]) > 0\n"
              f"    or ({FAILED}_count{sel} unless {FAILED}_count{sel} offset {fw})\n"
              f")")
    stale_expr = (f"(\n"
                  f"  (sum by (dag_id) (increase({SUCCESS}_count{sel}[{sw}])) == 0\n"
                  f"    unless on (dag_id) ({SUCCESS}_count{sel} unless {SUCCESS}_count{sel} offset {sw}))\n"
                  f"  or absent_over_time({SUCCESS}_count{sel}[{sw}])\n"
                  f")\n"
                  f'unless on (dag_id) ({RUNNING}{{dag_id="{dag}",task_id=~"{MAINTAIN_TASKS}"}} > 0)')
    slow = (f"max by (dag_id) (\n"
            f"  increase({SUCCESS}_sum{sel}[{iw}]) / increase({SUCCESS}_count{sel}[{iw}])\n"
            f"    or ({SUCCESS}_sum{sel} / {SUCCESS}_count{sel} unless {SUCCESS}_count{sel} offset {iw})\n"
            f") > {slow_s}")
    reference = f"its {timeout}-minute timeout" if timeout else f"its {interval}-minute schedule interval (it has no timeout)"
    return [
        (f"ProductRunFailed_{dag}", _rule(
            f"ProductRunFailed_{dag}", failed, labels, f"{p.id}: a run of DAG {dag} failed",
            f"A run of {dag} ended failed within the last {fw}; its task log says which task failed and why.")),
        (f"ProductStale_{dag}", _rule(
            f"ProductStale_{dag}", stale_expr, labels, f"{p.id}: no successful run in {sw}",
            f"No run of {dag} ({p.schedule.cron}, UTC) has succeeded in {sw} and no maintain or compact task "
            f"is running: the DAG is paused, held, failing or not being scheduled.", STALE_FOR)),
        (f"ProductRunSlow_{dag}", _rule(
            f"ProductRunSlow_{dag}", slow, {**labels, "severity": "warning"},
            f"{p.id}: runs of DAG {dag} near {reference}",
            f"The runs of {dag} that succeeded in the last {iw} took on average over {slow_s} s, "
            f"{round(SLOW_FRACTION * 100)}% of {reference}.")),
    ]


HEADER = """\
# Compiled by k2gen (platform/k2gen/k2gen/emit/alerts.py) from the release's contracts: never edit.
# Per-product alert rules (I-5, docs/development/phase-001-interfaces.md, "Per-product alerts").
"""


def emit(model: Model) -> dict[str, str]:
    groups = []
    for p in sorted(model.products, key=lambda p: p.id):
        if p.schedule is None:
            continue
        body = "\n\n".join(text for _, text in rules(p))
        groups.append(f"  - name: {_q('product:' + p.id)}\n    interval: 30s\n    rules:\n{body}")
    return {PATH: HEADER + ("groups:\n" + "\n\n".join(groups) + "\n" if groups else "groups: []\n")}
