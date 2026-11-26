"""
One DAG per file source (ADR-036), built from file_sources.json, which
`make generate` writes from each contract's `fileSource` custom property. Never
edit that file; change the contract.

Every file source's DAG is the same four tasks in a line, each one subcommand of
docker/lake/file_source.py in the Spark container (lake_script.py):

    fetch >> load >> audit >> publish

fetch lands the file and returns its landed key, which the other three read
from its XCom. A failed audit fails its task, so publish never starts and `main`
does not move; AirflowTaskFailed fires, and the runbook is
docs/runbooks/file-source-audit-failed.md. The rules of lake.py hold here too:
one run at a time, no replay of a missed run, a timeout that stops the driver.

With no file source declared, the file is `{}` and this builds no DAG.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from airflow.sdk import DAG
from lake_script import COMMON, LakeScript

CONFIG = Path(__file__).with_name("file_sources.json")
LANDED = "{{ ti.xcom_pull(task_ids='fetch') }}"
RETRY_DELAY = timedelta(minutes=5)
# (task, retries, execution timeout). load, audit and publish wait up to 600 s for the
# writer lock (file_source.LOCK_WAIT_S) before their Spark work. No retry of an audit: a
# failed audit is a finding, not a blip.
STEPS = (
    ("fetch", 2, timedelta(minutes=5)),
    ("load", 1, timedelta(minutes=20)),
    ("audit", 0, timedelta(minutes=20)),
    ("publish", 1, timedelta(minutes=20)),
)
# Every task's tries and the delays between them, plus slack.
RUN_TIMEOUT = sum(((r + 1) * t + r * RETRY_DELAY for _, r, t in STEPS), timedelta()) + timedelta(minutes=5)


def build(table: str, spec: dict) -> DAG:
    with DAG(
        dag_id=spec["dag_id"],
        schedule=spec["schedule"],
        dagrun_timeout=RUN_TIMEOUT,
        is_paused_upon_creation=False,
        default_args={"owner": spec["owner"]},
        **{**COMMON, "tags": ["lake", "file-source", spec["domain"]]},
    ) as dag:
        previous = None
        for step, retries, timeout in STEPS:
            task = LakeScript(
                task_id=step,
                script="file_source.py",
                args=[step, "--table", table, *([] if step == "fetch" else ["--object", LANDED])],
                # A file source is small; the ingest's 1536m default is for Kafka batches.
                env={} if step == "fetch" else {"K2_LAKE_DRIVER_MEMORY": "512m"},
                return_last_line=step == "fetch",
                retries=retries,
                retry_delay=RETRY_DELAY,
                execution_timeout=timeout,
            )
            if previous is not None:
                previous >> task
            previous = task
    return dag


for _table, _spec in json.loads(CONFIG.read_text()).items():
    globals()[_spec["dag_id"]] = build(_table, _spec)
