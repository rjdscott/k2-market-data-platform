"""Every page `make screenshots` captures, in the order the data moves, and the
gallery page (docs/operations/dashboards.md) rendered from them.

One dict per image. capture.py drives the browser from these; check.py and the
gallery read them too. Fields:

  file      PNG name under docs/images/dashboards/; the number keeps the order
  section   gallery heading the image sits under
  title     the image's own heading
  url       what an operator opens; capture.py adds Grafana's kiosk, theme and
            time-window parameters itself
  app       whose login the page needs: grafana, airflow, minio, or None
  open      a hook in capture.py for a page that needs more than a goto
  ready     a selector that only matches once the page shows real data
  height    viewport height in px; the width is always 1600
  callouts  (number, target, what it points at[, "left"]): a marker on the
            image and a line in its caption. The target is a Playwright
            selector, or an (x, y) point; "left" puts the marker on the
            target's left, for a thin target whose right side is busy.
  no_data   Grafana panel titles allowed to read "No data"
  mask      selectors painted over before the screenshot
  read      what an operator reads from the page
  leads_to  the runbook (or doc) the page sends you to, from the repo root

Stdlib only: check.py and the gallery run on the host without Playwright.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "docs/images/dashboards"
MANIFEST = IMAGES / "manifest.json"
GALLERY = ROOT / "docs/operations/dashboards.md"


def panel(title):
    """A Grafana panel by its title (Grafana's own e2e selector)."""
    return f"section[data-testid='data-testid Panel header {title}']"


SHOTS = [
    dict(
        file="01-pipeline-overview.png", section="Start here", title="K2 Pipeline Overview",
        url="http://localhost:3000/d/k2-pipeline-overview", app="grafana", height=1200,
        ready=panel("Service Health"),
        callouts=[
            (1, panel("Service Health"), "every scrape target, up or down"),
            (2, panel("Total Trade Records Produced (all exchanges)"), "trade records each venue's capture produced per second"),
            (3, panel("ClickHouse Row Insert Rate (rows/sec)"), "rows the hot tier inserts from Redpanda"),
            (4, panel("CPU used by the pipeline (cores)"), "CPU each pipeline container uses, from cAdvisor"),
        ],
        read="The page to open first. All tiles green and three moving lines say capture, the bus and "
             "the hot tier are live; the Resources row says what that costs. A line gone flat says "
             "which of the pages below to open next.",
        leads_to="docs/runbooks/failure-recovery.md",
    ),
    dict(
        file="02-capture.png", section="Capture", title="K2 Capture (v3)",
        url="http://localhost:3000/d/k2-l2-capture", app="grafana", height=1060,
        ready=panel("Capture Process Up"),
        read="Per venue: is the process up, seconds since its last frame, and any reconnects, sequence "
             "gaps, checksum failures or resyncs in the last hour. Below, frames and bytes per second, "
             "and records produced against records the broker acknowledged: a gap between those two "
             "lines is records that never reached Redpanda.",
        leads_to="docs/runbooks/capture-feed-stale.md",
    ),
    dict(
        file="03-redpanda-topics.png", section="Bus", title="Redpanda Console: topics",
        url="http://localhost:8080/topics", app=None, height=1000,
        ready="text=market.crypto.v3.raw.binance",
        read="Every v3 topic, raw, trades and book for each venue, with its partitions and the bytes it "
             "holds. Raw is what the lake ingests; the trades and book topics feed ClickHouse. Console "
             "has no dark theme, so this one is light.",
        leads_to="docs/runbooks/redpanda.md",
    ),
    dict(
        file="04-lake.png", section="Lake ingest and tables", title="K2 Lake (v3)",
        url="http://localhost:3000/d/k2-lake", app="grafana", height=1950,
        ready=panel("Ingest lag"),
        callouts=[
            (1, panel("Ingest lag"), "age of the newest Kafka record in raw.messages"),
            (2, panel("raw.messages commit age"), "time since the archive last committed"),
            (3, panel("Ingest backlog by topic"), "records the last run left unread, zero when caught up"),
            (4, panel("CPU used by the lake tier (cores)"), "Spark, MinIO, Lakekeeper and the exporter"),
        ],
        read="Is the lake current: ingest lag and commit age saw-tooth under the 5-minute schedule, the "
             "backlog sits at zero, audit failures at 0. In the Resources row, each ingest is a Spark "
             "CPU spike every five minutes.",
        leads_to="docs/runbooks/lake-ingest-lag.md",
    ),
    dict(
        file="05-spark-master.png", section="Lake ingest and tables", title="Spark master",
        url="http://localhost:18080", app=None, height=700,
        ready="text=ALIVE",
        read="The batch engine is up: one worker, its cores and memory. It lists no applications: the "
             "lake jobs start their own local-mode driver in this container with `docker exec`, and "
             "show up on the Airflow pages instead. Spark's UI has no dark theme.",
        leads_to="docs/runbooks/lake-recovery.md",
    ),
    dict(
        file="06-lakekeeper-gold.png", section="Lake ingest and tables", title="Lakekeeper: the gold namespace",
        url="http://localhost:18181/ui", app=None, open="lakekeeper", height=900,
        ready="text=book_top20",
        read="The Iceberg REST catalog: warehouse k2, its five namespaces (raw, bronze, silver, gold, "
             "audit) and the gold tables ClickHouse reloads from. The red banner is accurate: the "
             "catalog runs without authentication, published on loopback only.",
        leads_to="docs/runbooks/lake-recovery.md",
    ),
    dict(
        file="07-minio-lake-bucket.png", section="Lake ingest and tables", title="MinIO: the k2-lake bucket",
        url="http://localhost:9001/browser/k2-lake", app="minio", height=800,
        ready="text=warehouse",
        read="The object store under the lake: bucket k2-lake, its size and object count. Every Iceberg "
             "data and metadata file sits under warehouse/; the archive is kept forever, so this size "
             "only grows.",
        leads_to="docs/runbooks/lake-disk-usage-high.md",
    ),
    dict(
        file="08-clickhouse.png", section="Hot tier", title="ClickHouse Overview (gold)",
        url="http://localhost:3000/d/clickhouse-v2", app="grafana", height=780,
        ready=panel("Query Rate (queries/sec)"),
        read="Queries and failed queries per second, resident memory, inserts and rows inserted per "
             "second, background merges. Inserts never stop while capture runs: the Kafka engine feeds "
             "gold straight from the topics.",
        leads_to="docs/runbooks/failure-recovery.md",
    ),
    dict(
        file="09-clickhouse-query.png", section="Hot tier", title="ClickHouse: a query on gold.trades",
        url="http://localhost:8123/play", app=None, open="clickhouse", height=560,
        ready="#data_div td:has-text('kraken')",
        mask=["#password"],
        read="The served tier answering, as the read-only `quant` user: trades per venue in the last "
             "hour and the newest exchange timestamp. A stale `newest` while capture is healthy is "
             "the case ClickHouseGoldFeedStale pages on.",
        leads_to="docs/runbooks/clickhouse-rebuild-from-lake.md",
    ),
    dict(
        file="10-airflow-dags.png", section="Scheduling", title="Airflow: DAGs",
        url="http://localhost:8090/dags", app="airflow", height=720,
        ready="text=lake_maintenance",
        # A run in flight shows a spinner as its state badge, most of every 5 minutes.
        busy_ok=True,
        read="The three lake DAGs: lake_ingest every 5 minutes, lake_maintenance daily at 03:00, "
             "lake_rebuild on demand only. Each shows its last run and a strip of recent run states.",
        leads_to="docs/runbooks/airflow-scheduling.md",
    ),
    dict(
        file="11-airflow-lake-ingest.png", section="Scheduling", title="Airflow: lake_ingest",
        url="http://localhost:8090/dags/lake_ingest", app="airflow", height=820,
        ready="text=Last 10 Dag Runs",
        callouts=[
            (1, "[data-testid='task-ingest']", "the grid: one column per run, coloured by state", "left"),
            (2, "text=Last 10 Dag Runs", "run duration of the last ten runs"),
            (3, "text=Failed Tasks", "failed tasks in the last 24 hours"),
        ],
        read="Each 5-minute ingest run as a column in the grid, beside the duration of the last ten. A "
             "run that finds maintenance holding the writer lock exits 75 and shows as skipped, not "
             "failed.",
        leads_to="docs/runbooks/airflow-scheduling.md",
    ),
    dict(
        file="12-airflow-task-log.png", section="Scheduling", title="Airflow: one ingest run's log",
        url="http://localhost:8090/dags/lake_ingest/runs", app="airflow", open="airflow_task_log", height=1000,
        ready="[data-testid='virtualized-list'] >> text=INFO",
        read="The log of the latest successful ingest run: Spark's stage progress, then the topics it "
             "read with their schema ids. The first place to look when AirflowTaskFailed fires.",
        leads_to="docs/runbooks/airflow-scheduling.md",
    ),
    dict(
        file="13-airflow-dashboard.png", section="Scheduling", title="K2 Airflow",
        url="http://localhost:3000/d/k2-airflow", app="grafana", height=1050,
        ready=panel("DAG run duration"),
        no_data=["Skips", "Task failures"],
        read="Run duration and lateness per DAG, task failures and skips, scheduler heartbeat and DAG "
             "parse time, from Airflow's statsd metrics. The three Airflow alerts read these series. "
             "Skips and failures read \"No data\" when there were none.",
        leads_to="docs/runbooks/airflow-scheduling.md",
    ),
    dict(
        file="14-prometheus-alerts.png", section="Alerting", title="Prometheus: alerts",
        url="http://localhost:9090/alerts", app=None, height=1000,
        ready="text=capture-alerts.yml",
        read="Every alert rule, grouped by file, with its state. There is no Alertmanager: this page and "
             "Grafana are where a firing alert is seen, and each rule's annotations name its runbook.",
        leads_to="docs/runbooks/README.md",
    ),
    dict(
        file="15-prometheus-targets.png", section="Alerting", title="Prometheus: targets",
        url="http://localhost:9090/targets", app=None, height=1000,
        ready="text=capture-binance",
        read="Every scrape job and whether its last scrape succeeded. A target down here is the input "
             "CaptureDown, ClickHouseDown and LakeExporterDown fire on.",
        leads_to="docs/runbooks/failure-recovery.md",
    ),
    dict(
        file="16-resources.png", section="Resources", title="K2 Resources",
        url="http://localhost:3000/d/k2-resources", app="grafana", height=1520,
        ready=panel("Every container against its compose limit"),
        callouts=[
            (1, panel("CPU limits against the 25-core ceiling"), "declared CPU limits of the running containers, against ADR-045's ceiling"),
            (2, panel("Every container against its compose limit"), "each container's CPU and memory against the limit read from its cgroup"),
            (3, panel("CPU throttled share"), "containers their CPU limit is holding back"),
        ],
        read="What the stack uses against what it declares. Limits are read from each container's "
             "cgroup by cAdvisor, so the table follows docker-compose.yml with no edit here. Sustained "
             "throttling, or memory near its limit, is a limit to raise, inside the budget that "
             "`python3 scripts/check-budget.py` gates.",
        leads_to="docs/operations/docker-resources.md",
    ),
]


def load_manifest(path=MANIFEST):
    """{file: {"file", "url", "captured_at", "git_sha"}}, empty when there is none."""
    if not path.exists():
        return {}
    return {s["file"]: s for s in json.loads(path.read_text())["shots"]}


def render_gallery(manifest):
    """docs/operations/dashboards.md, from SHOTS and the manifest's capture records."""
    out = [
        "# Dashboards and consoles",
        "",
        "<!-- Generated by `make screenshots` from scripts/screenshots/shots.py. Edit there. -->",
        "",
        "What each screen of a running K2 stack shows, in the order the data moves: capture, the bus,",
        "the lake, the hot tier, scheduling, alerting, resources. Every image is a picture of a live",
        "run at the date in its caption; the figures in it are venue-derived, true at that moment, and",
        "for illustration only ([data terms](../../README.md#what-this-is-and-data-terms)). Numbered",
        "markers are named under each image.",
        "",
        "`make screenshots` recaptures every image and rewrites this page, with the stack up;",
        "`make screenshots ONLY=04-lake` redoes one. Before a release,",
        "`python3 scripts/screenshots/check.py --since <date>` fails on any image missing or older.",
    ]
    section = None
    for s in SHOTS:
        if s["section"] != section:
            section = s["section"]
            out += ["", f"## {section}"]
        out += ["", f"### {s['title']}", ""]
        rec = manifest.get(s["file"])
        if rec:
            out += [f"![{s['title']}](../images/dashboards/{s['file']})", ""]
            caption = f"*Captured {rec['captured_at'][:10]} at `{rec['git_sha']}`.*"
            for n, _, what, *_ in s.get("callouts", []):
                caption += f" **{n}** {what}."
            out += [caption, ""]
        else:
            out += ["*No image: the last `make screenshots` did not capture this page.*", ""]
        rel = os.path.relpath(ROOT / s["leads_to"], GALLERY.parent)
        out += [s["read"], "", f"Open: <{s['url']}>. Leads to: [{Path(rel).name}]({rel})."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    # Rewrite the gallery from the existing manifest, without recapturing.
    GALLERY.write_text(render_gallery(load_manifest()))
    print(f"wrote {GALLERY.relative_to(ROOT)}")
