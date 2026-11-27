"""Emits model.pipeline_path(<product id>) (contracts/schema/pipeline.schema.json) for every
product with a schedule, model.DAGS (dags.schema.json) and model.ONESHOTS (oneshots.schema.json:
per kind the image from model.images, limits, env names, networks, secret mounts; fetch never on
k2-catalog). Inputs at model.input_relation; maintenance reads model.RETENTION, not a copy.
Filled by step S16.

What the phase 002 DAG factory reads, and nothing it decides at run time:
- The connector is copied as its product file declares it. Its url, path, key and params were
  held to model.TEMPLATE when the document loaded, and pipeline.schema.json holds the copy to it
  again in `k2gen release`, so no macro but `{{ds}}` reaches the release; the fetch renders that
  one, this module renders nothing.
- A product with no schedule (the paused serving-only hot tier) has no pipeline and no DAG, and
  says so on stderr. A dbt-only product (the seed-built access canary) has a build and no
  connector, landing or bronze.
- A DAG's tasks are the subset of TASKS its pipeline needs, in that order: release.needs compares
  them, so a description, a cron or a connector url never changes the DAG's shape. Pools
  (lake_spark 1, fetch 1, dbt 2) and max_active_runs=1 are the factory's constants
  (docs/development/phase-001-interfaces.md); dags.schema.json has no field for them.
- Expire's `older_than => TIMESTAMP '1970-01-01 00:00:00'` and retention are run-time contracts
  (the same page, compiled/lake/retention.json): the pipeline carries only maintain's cron.
- A kafka connector (I-1, docs/development/phase-001-interfaces.md) lands lake.<fence>.landing and
  stages its one bronze table from it, keyed (kafka_partition, kafka_offset), its values the
  record's timestamp, its writer schema id and the decoded envelope's columns, no fetch task. A public-api connector runs the fetch one-shot
  on k2-egress as a file kind does, pagination none only (k2gen check connectors.*).
- A one-shot kind with no image in --images is left out with a line on stderr: phase 001 builds
  only tools; fetch and dbt-runner arrive in phase 002.
"""

import hashlib
import json
import sys
from typing import Any

from k2gen.model import (
    AUDIT_BRANCH,
    AUDIT_CHECKS,
    DAGS,
    KAFKA_LANDING,
    LANDING_BUCKET,
    LANDING_FILES,
    LINEAGE_COLUMN,
    ONESHOTS,
    SECRET_FILES,
    Model,
    Product,
    Table,
    build_relation,
    builder,
    dag_id,
    fence,
    input_relation,
    lake_relation,
    landing_prefix,
    password_mount,
    pipeline_path,
    publisher,
)

# Every task id in run order (dags.schema.json): resolve hands the release on by XCom; fetch runs
# the fetch one-shot (file and API kinds); load, audit, publish are bronze's Spark write-audit-publish;
# build is the dbt wrapper, which publishes its outputs' tag itself; expire drops the snapshots the
# wrapper's XCom names; record writes the run record on every outcome; maintain is the daily task.
TASKS = ("resolve", "fetch", "load", "audit", "publish", "build", "expire", "record", "maintain")

_CA = next(s.mount for s in SECRET_FILES if s.path == "tls/ca.pem")
# Each one-shot kind as docker-compose.yml's stubs declare it (A13; S22 checks the two agree). Names
# only. dbt-runner's env is the wrapper's, exactly; a connector's own credential names
# (authHeaderEnv, credentialsEnv, keyFileEnv) stay in its pipeline.json, passed to its fetch only.
ONESHOT_KINDS: dict[str, dict[str, Any]] = {
    "tools": {"cpus": "0.25", "memory": "256M", "networks": ["k2-net", "k2-catalog"], "env": ["K2_RELEASE"],
              "secrets": [_CA]},
    "dbt-runner": {"cpus": "0.5", "memory": "768M", "networks": ["k2-catalog"],
                   "env": ["K2_TRINO_HOST", "K2_TRINO_INTERNAL_PORT", "K2_TRINO_USER", "K2_PUB_USER", "K2_RUNS_USER",
                           "K2_CATALOG_URI", "K2_RELEASE"],
                   "secrets": [password_mount("dbt_*"), password_mount("pub_*"), password_mount("svc.runs"), _CA]},
    # fetch on k2-egress alone (2026-11-27): a parser reaches the internet and nothing of the stack
    "fetch": {"cpus": "0.5", "memory": "512M", "networks": ["k2-egress"], "env": ["K2_RELEASE"], "secrets": []},
}
# A kafka bronze row's lineage to its landing batch (I-1): landing columns, never source values.
# connectors.kafka-bronze-shape holds the bronze table to its lead, envelope and lineage columns,
# and pipeline.schema.json the compiled key and values, so a kafka bronze compiles as kafka.py stages it.
KAFKA_LINEAGE = ("batch_id", "landed_at", "run_id", "release_id")
MAINTAIN_FIRST_HOUR, MAINTAIN_HOURS = 2, 4  # every maintain cron falls in 02:00 to 05:59 UTC


def maintain_cron(product_id: str) -> str:
    """The product's daily maintenance minute in the window, from a hash of its id: stable while
    other products come and go."""
    # ponytail: two products can draw one minute; the lake_spark pool of 1 queues them. Spread by
    # sorted index if a window per DAG is ever needed.
    minute = int(hashlib.sha256(product_id.encode()).hexdigest(), 16) % (60 * MAINTAIN_HOURS)
    return f"{minute % 60} {MAINTAIN_FIRST_HOUR + minute // 60} * * *"


def _json(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _bronze(p: Product, t: Table) -> dict[str, Any]:
    key = sorted((c for c in t.columns if c.primary_key), key=lambda c: c.primary_key_position or 0)
    lineage = KAFKA_LINEAGE if p.connector and p.connector.kind == "kafka" else (LINEAGE_COLUMN,)
    return {
        "table": lake_relation(p.name, t.name),
        "key": [c.name for c in key],
        "values": [c.name for c in t.columns if not c.primary_key and c.name not in lineage],
        "decimal": [c.name for c in t.columns if c.physical_type.startswith("decimal(")],
        "patterns": {c.name: c.pattern for c in t.columns if c.pattern},
        "audits": [q.implementation for q in t.quality if q.type == "custom" and q.engine == "k2-connector"],
        "branch": AUDIT_BRANCH,
        "checks": lake_relation(p.name, AUDIT_CHECKS),
    }


def product_pipeline(model: Model, p: Product) -> dict[str, Any]:
    """One product's pipeline.json; `p.schedule` is set."""
    assert p.schedule is not None  # emit skips a product without one
    schedule: dict[str, Any] = {"cron": p.schedule.cron}
    if p.schedule.timeout_minutes is not None:
        schedule["timeoutMinutes"] = p.schedule.timeout_minutes
    tables = {t.contract_id: t for q in model.products for t in q.tables}
    inputs = []
    for port in p.input_ports:
        if port.contract_id not in tables:
            raise ValueError(f"{p.id}: input {port.contract_id} is no table of the set (k2gen check: docs.reference-unresolved)")
        inputs.append({"contractId": port.contract_id, "relation": input_relation(tables[port.contract_id]),
                       "required": port.required, "maxLagMinutes": port.max_lag_minutes,
                       "whenAbsent": port.when_absent})
    doc: dict[str, Any] = {
        "product": p.id, "fence": fence(p.name), "domain": p.domain, "status": p.status, "dagId": dag_id(p.name),
        "schedule": schedule, "inputs": inputs, "maintain": {"cron": maintain_cron(p.id)},
    }
    if p.connector:
        doc["connector"] = {"kind": p.connector.kind, **p.connector.settings}
        doc["landing"] = ({"table": lake_relation(p.name, KAFKA_LANDING)} if p.connector.kind == "kafka" else
                          {"bucket": LANDING_BUCKET, "prefix": landing_prefix(p.domain, p.name), "log": lake_relation(p.name, LANDING_FILES)})
        doc["bronze"] = [_bronze(p, t) for t in p.tables if t.build.kind == "connector"]
    if code := [t.name for t in p.tables if t.build.kind == "code"]:
        raise ValueError(f"{p.id}: {', '.join(code)} built by code, which pipeline.schema.json's build (engine dbt) cannot run yet")
    if outputs := [build_relation(p.domain, p.name, t.name) for t in p.tables if t.build.kind == "dbt"]:
        doc["build"] = {"engine": "dbt", "selector": dag_id(p.name), "writer": builder(p.name),
                        "publisher": publisher(p.name), "outputs": outputs}
    return doc


def tasks(doc: dict[str, Any]) -> list[str]:
    """The DAG's task ids for one pipeline.json, in TASKS order."""
    needed = {"resolve", "record", "maintain"}
    if "connector" in doc:
        needed |= {"load", "audit", "publish"} | ({"fetch"} if doc["connector"]["kind"] != "kafka" else set())
    if "build" in doc:
        needed |= {"build", "expire"}
    return [t for t in TASKS if t in needed]


def emit(model: Model) -> dict[str, str]:
    files: dict[str, str] = {}
    dags: list[dict[str, Any]] = []
    for p in sorted(model.products, key=lambda p: p.id):
        if p.schedule is None:
            print(f"k2gen release: NO PIPELINE {p.id}: it has no schedule, so no DAG runs it", file=sys.stderr)
            continue
        doc = product_pipeline(model, p)
        files[pipeline_path(p.id)] = _json(doc)
        dags.append({"dagId": doc["dagId"], "product": p.id, "status": p.status, "schedule": p.schedule.cron,
                     "pipeline": pipeline_path(p.id), "tasks": tasks(doc)})
    files[DAGS] = _json({"dags": sorted(dags, key=lambda d: d["dagId"])})
    oneshots: dict[str, Any] = {}
    for kind, spec in ONESHOT_KINDS.items():
        if kind in model.images:
            oneshots[kind] = {"image": model.images[kind], **spec}
        else:
            print(f"k2gen release: NO ONESHOT {kind}: --images names no image for it", file=sys.stderr)
    if oneshots:
        files[ONESHOTS] = _json({"oneshots": oneshots})
    return files
