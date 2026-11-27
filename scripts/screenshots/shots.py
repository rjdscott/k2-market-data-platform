"""Every page `make screenshots` captures, in the order the data moves, and the
gallery page (docs/operations/dashboards.md) rendered from them.

One dict per image. capture.py drives the browser from these; check.py and the
gallery read them too. Fields:

  file      PNG name under docs/images/dashboards/; the number keeps the order
  section   gallery heading the image sits under
  title     the image's own heading
  url       what an operator opens; capture.py adds Grafana's kiosk, theme and
            time-window parameters itself
  app       whose login the page needs: grafana, minio, or None
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
            (3, panel("Redpanda Kafka Produced Bytes / sec"), "bytes per second Redpanda accepts from the captures"),
            (4, panel("CPU used by the pipeline (cores)"), "CPU each pipeline container uses, from cAdvisor"),
        ],
        read="The page to open first. All tiles green and moving lines say capture and the bus are "
             "live; the Resources row says what that costs. A line gone flat says which of the pages "
             "below to open next.",
        leads_to="docs/runbooks/failure-recovery.md",
    ),
    dict(
        file="02-capture.png", section="Capture", title="K2 Capture (v3)",
        url="http://localhost:3000/d/k2-l2-capture", app="grafana", height=1060,
        ready=panel("Capture Process Up"),
        # Books are paused (ADR-050): no checksum is computed, so the counter has no series.
        no_data=["Checksum Failures (1h)"],
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
             "holds. Nothing consumes them while the old lake ingest is retired and ClickHouse is "
             "paused; phase 004's connector reads raw again. Console has no dark theme, so this one "
             "is light.",
        leads_to="docs/runbooks/redpanda.md",
    ),
    # 04 (the K2 Lake dashboard) and 06 (Lakekeeper's gold namespace) retired with
    # the old lake ingest (legacy/v3-lake-ingest/); their numbers are not reused.
    dict(
        file="05-spark-history.png", section="Lake", title="Spark History Server",
        url="http://localhost:18080", app=None, height=700,
        ready="text=Event log directory",
        read="The Spark container's finished jobs, newest first, each a local-mode driver started in "
             "it by `docker exec`. Open one for its stages, which is where a failed one is read after "
             "the fact. Its event-log cleaner keeps two days. No job runs while the old lake ingest is "
             "retired. Spark's UI has no dark theme.",
        leads_to="docs/runbooks/upgrade-a-pinned-component.md",
    ),
    dict(
        file="07-minio-lake-bucket.png", section="Lake", title="MinIO: the k2-lake bucket",
        url="http://localhost:9001/browser/k2-lake", app="minio", height=800,
        ready="text=k2-lake",   # the bucket is empty until phase 002 writes the first table
        read="The object store under the lake: bucket k2-lake, its size and object count. Every Iceberg "
             "data and metadata file of every warehouse sits in it. The old pipeline's tables were "
             "dropped after a verified copy (`make drop`): no live crypto data lands here until "
             "phase 004.",
        leads_to="docs/runbooks/lake-backup-and-restore.md",
    ),
    # 08 and 09, the ClickHouse hot tier, retired with its pause (ADR-057); 10 to
    # 12, the old lake DAGs in Airflow's UI, with the old lake ingest
    # (legacy/v3-lake-ingest/). The numbers are not reused, so the rest keep
    # their files.
    dict(
        file="13-airflow-dashboard.png", section="Scheduling", title="K2 Airflow",
        url="http://localhost:3000/d/k2-airflow", app="grafana", height=1050,
        ready=panel("DAG run duration"),
        # No DAG runs until phase 002, so every per-DAG panel is empty.
        no_data=["DAG run duration", "Run lateness", "Task failures", "Skips"],
        read="Run duration and lateness per DAG, task failures and skips, scheduler heartbeat and DAG "
             "parse time, from Airflow's statsd metrics. The two Airflow alerts read these series. "
             "Until phase 002 adds the first product DAG only the heartbeat and the parse loop move; "
             "the other panels read \"No data\".",
        leads_to="docs/runbooks/airflow-scheduling.md",
    ),
    dict(
        file="14-prometheus-alerts.png", section="Alerting", title="Prometheus: alerts",
        url="http://localhost:9090/alerts", app=None, height=1000,
        ready="text=capture-alerts.yml",
        read="Every alert rule, grouped by file, with its state. A firing alert also goes to Slack through "
             "Alertmanager; each rule's annotations name its runbook.",
        leads_to="docs/runbooks/README.md",
    ),
    dict(
        file="15-prometheus-targets.png", section="Alerting", title="Prometheus: targets",
        url="http://localhost:9090/targets", app=None, height=1000,
        ready="text=capture-binance",
        read="Every scrape job and whether its last scrape succeeded. A capture target down here is "
             "the input CaptureDown fires on.",
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

# A paragraph under a section heading, for what the images alone would mislead on.
NOTES = {
    "Capture": "While order-book capture is paused ([ADR-050](../adr/ADR-050-order-book-capture-paused-during-platform-build.md)), the Book row of the capture\n"
               "dashboard shows no depth or level data for a venue that is off; its \"Book Capture\" panel says\n"
               "which venues those are.",
}


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
        "the lake, scheduling, alerting, resources. Every image is a picture of a live",
        "run at the date in its caption; the figures in it are venue-derived, true at that moment, and",
        "for illustration only ([data terms](../../README.md#what-this-is-and-data-terms)). Numbered",
        "markers are named under each image.",
        "",
        "`make screenshots` recaptures every image and rewrites this page, with the stack up;",
        "`make screenshots ONLY=02-capture` redoes one. Before a release,",
        "`python3 scripts/screenshots/check.py --since <date>` fails on any image missing or older.",
    ]
    section = None
    for s in SHOTS:
        if s["section"] != section:
            section = s["section"]
            out += ["", f"## {section}"]
            if section in NOTES:
                out += ["", NOTES[section]]
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
