"""
`dbt_marts`: the domain marts on Trino (ADR-035, ADR-044), hourly at minute 10.

astronomer-cosmos renders the dbt project (dbt/, mounted read-only at
/opt/k2/dbt, as docker/airflow/dags is) into one task group per selector in
dbt/selectors.yml, that is one per domain with marts, and runs every task in
it as that domain's writer, K2_TRINO_USER=dbt_<domain>: the only user Trino
lets write the domain's catalog. Inside a group, one task per dbt node, in the
graph's order: `dbt build --select <node>` for a model (its unit tests, the
model, its data tests), and for a lake table a mart reads, `dbt source
freshness` and its source tests. A failed node stops its descendants and
nothing else.

Each dbt command runs as /opt/k2/dbt-venv/bin/dbt, a virtualenv of its own
built from dbt/uv.lock (docker/airflow/Dockerfile), in a temporary directory
cosmos makes of symlinks to the project, without its target/ and logs/: dbt
writes those there, and the mount stays read-only. The packages are the
image's, installed at build time (K2_DBT_PACKAGES).

Once a day, in the 04:10 run, each domain's writer then expires its marts'
snapshots and orphan files at the 7-day floor (dbt/macros/expire_mart_snapshots.sql).

Every dbt invocation that runs a test records its results in its domain's own
dq.dbt_test_results (dbt/macros/record_test_results.sql). That table is created,
if absent, by a task of the domain's own before any other: two of its tasks whose
hooks both created it at once could fail (ADR-046 Risks). The governance domain
builds last, once every other domain's build has finished however it ended, so
its view over all of those tables (governance.quality_results.dq_results) sees
this run's results and its tests run on them (ADR-046).

What the lake DAGs' rules (lake.py) become here: one run at a time, no
catch-up (a mart is rebuilt whole from the lake, so a missed hour is the next
hour's), and a run timeout inside the hour. At most two tasks at once, so the
scheduler's four LocalExecutor slots always leave two for the lake.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, TriggerRule, task
from cosmos import DbtTaskGroup, ExecutionConfig, ProfileConfig, ProjectConfig, RenderConfig
from cosmos.constants import (
    ExecutionMode,
    InvocationMode,
    LoadMode,
    SourceRenderingBehavior,
    TestBehavior,
)
from cosmos.operators.local import DbtRunOperationLocalOperator

DBT_PROJECT = Path("/opt/k2/dbt")
DBT = "/opt/k2/dbt-venv/bin/dbt"
# The hour (UTC) whose run also does the snapshot maintenance: after
# lake_maintenance's 03:00 start, which does the same for the lake with Spark.
MAINTENANCE_HOUR = 4
# The domain whose view reads every other domain's test results: it builds after them.
GOVERNANCE = "governance"


def env(domain: str) -> dict[str, str]:
    """What every dbt command of one domain runs with (dbt/profiles.yml reads the first three)."""
    return {
        "K2_TRINO_USER": f"dbt_{domain}",
        "K2_TRINO_HOST": "trino",
        "K2_TRINO_PORT": "8080",
        "K2_DBT_PACKAGES": "/opt/k2/dbt_packages",
    }


def selectors() -> list[str]:
    """One per domain with marts, generated from the contracts (make generate)."""
    return [s["name"] for s in yaml.safe_load((DBT_PROJECT / "selectors.yml").read_text())["selectors"]]


PROJECT = ProjectConfig(dbt_project_path=DBT_PROJECT, install_dbt_deps=False)
PROFILE = ProfileConfig(profile_name="k2", target_name="trino", profiles_yml_filepath=DBT_PROJECT / "profiles.yml")
EXECUTION = ExecutionConfig(
    execution_mode=ExecutionMode.LOCAL, invocation_mode=InvocationMode.SUBPROCESS, dbt_executable_path=DBT
)


def run_operation(domain: str, macro: str, task_id: str) -> DbtRunOperationLocalOperator:
    """`dbt run-operation <macro>` as the domain's writer."""
    return DbtRunOperationLocalOperator(
        task_id=task_id,
        macro_name=macro,
        project_dir=str(DBT_PROJECT),
        profile_config=PROFILE,
        dbt_executable_path=DBT,
        invocation_mode=InvocationMode.SUBPROCESS,
        env=env(domain),
        append_env=True,
        install_deps=False,
        emit_datasets=False,
    )


with DAG(
    dag_id="dbt_marts",
    schedule="10 * * * *",
    start_date=datetime(2026, 11, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=2,
    # The whole run, maintenance included, ends before the next slot starts.
    dagrun_timeout=timedelta(minutes=50),
    is_paused_upon_creation=False,
    default_args={"retries": 0, "execution_timeout": timedelta(minutes=20)},
    tags=["dbt"],
):

    @task.short_circuit
    def maintenance_hour(dag_run=None) -> bool:
        """True in the run for MAINTENANCE_HOUR: its maintenance tasks run, every other run skips them.

        A scheduled run's logical date is its cron time. A manual run has the one it
        was given, or none and then its trigger time, so `airflow dags test dbt_marts
        2026-11-25T04:10:00+00:00` runs the maintenance whenever it is started.
        """
        when = dag_run.logical_date or dag_run.run_after
        return when.astimezone(timezone.utc).hour == MAINTENANCE_HOUR

    builds = {}
    for domain in selectors():
        build = builds[domain] = DbtTaskGroup(
            group_id=domain,
            project_config=PROJECT,
            profile_config=PROFILE,
            execution_config=EXECUTION,
            render_config=RenderConfig(
                load_method=LoadMode.DBT_LS,
                selector=domain,
                test_behavior=TestBehavior.BUILD,
                source_rendering_behavior=SourceRenderingBehavior.WITH_TESTS_OR_FRESHNESS,
                invocation_mode=InvocationMode.SUBPROCESS,
                dbt_executable_path=DBT,
                env_vars=env(domain),
                emit_datasets=False,
            ),
            operator_args={"env": env(domain), "append_env": True, "install_deps": False, "emit_datasets": False},
        )
        maintain = run_operation(domain, "expire_mart_snapshots", f"{domain}_expire_mart_snapshots")
        build >> maintenance_hour.override(task_id=f"{domain}_maintenance_hour")() >> maintain
        run_operation(domain, "create_test_results_table", f"{domain}_test_results_table") >> build

    # all_done: a failed test elsewhere is exactly what governance's view should show.
    others = [b for d, b in builds.items() if d != GOVERNANCE]
    if GOVERNANCE in builds and others:
        others >> EmptyOperator(task_id="domains_built", trigger_rule=TriggerRule.ALL_DONE) >> builds[GOVERNANCE]
