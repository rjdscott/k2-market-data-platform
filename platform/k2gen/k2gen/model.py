"""The v2 document model, its schemas, and the names every step shares (phase 001).

Frozen by the interfaces steps S00a and S00c: a type, constant or helper here, or a schema
in contracts/schema/, changes only through the orchestrator (platform/k2gen/INTERFACES.md).
Step S04 fills `load`. Everything else has its real body now, because every emitter, check
and script names, quotes, parses and dispatches through it.

The tested-data design is ADR-061's, as its gate (docs/research/2026-11-26-tested-data-view-gate.md)
amended it: a definer view over the `audited` tag, owned by a publisher that has catalog
access `all` on the reader catalog; `svc.platform` holds no privilege in a reader namespace.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

import jsonschema
import yaml
from referencing import Registry, Resource

REPO = Path(__file__).resolve().parents[3]  # the compiler's own tree, not the document set's
SCHEMA_DIR = REPO / "contracts" / "schema"
ODPS_SCHEMA = "odps-json-schema-v1.1.0.json"
ODCS_SCHEMA = "odcs-json-schema-v3.2.0-20261117.json"

# The schemas each kind of document or release file is validated against, in order: the
# vendored standard first where there is one, then K2's profile (k2-v2.schema.json).
SCHEMAS: Mapping[str, tuple[str, ...]] = {
    "domains": ("k2-v2.schema.json#/$defs/domains",),
    "product": (ODPS_SCHEMA, "k2-v2.schema.json#/$defs/product"),
    "contract": (ODCS_SCHEMA, "k2-v2.schema.json#/$defs/contract"),
    "grants": ("k2-v2.schema.json#/$defs/grants",),
    "roles": ("k2-v2.schema.json#/$defs/roles",),
    "principals": ("k2-v2.schema.json#/$defs/principals",),
    "expectations": ("expectations.schema.json",),
    "provenance": ("provenance.schema.json",),
    "manifest": ("manifest.schema.json",),
    "images": ("manifest.schema.json#/$defs/images",),  # the --images input of k2gen release
    "pipeline": ("pipeline.schema.json",),
    "catalog-product": ("catalog-product.schema.json",),
    "catalog-index": ("catalog-product.schema.json#/$defs/index",),
    "backup-set": ("backup-set.schema.json",),
    "run-record": ("run-record.schema.json",),
    "storage-plan": ("storage-plan.schema.json",),
    "dags": ("dags.schema.json",),
    "oneshots": ("oneshots.schema.json",),
    "deploy-record": ("deploy-record.schema.json",),
}

# --- Where documents live (A2) ---------------------------------------------------------

DOMAINS = "contracts/domains.yaml"
ROLES = "contracts/access/roles.yaml"
PRINCIPALS = "contracts/access/principals.yaml"
EXPECTATIONS = "tests/access/expectations.yaml"
COMPOSE = "docker-compose.yml"  # the tag's compose file: the release runs it (A1)
V2_PRODUCT_FILE = "product.odps.yaml"  # its directory is a v2 product; the old validator skips it
# What `git archive <tag>` extracts into a document set; a root the tag lacks (domains/ before
# its first parser) is skipped, never an error. The expectations are in it so the coverage
# check (S07) stays a pure function of the set.
DOCUMENT_ROOTS = ("contracts/", "domains/", EXPECTATIONS, COMPOSE)

# --- Release layout (A1) ---------------------------------------------------------------

RELEASES_MOUNT = "/k2/releases"  # the releases root in every container, read-only but in tools
DEPLOY_LOG = "deploy-log.jsonl"  # at the releases root: one deploy-record per line (S19)
MANIFEST = "manifest.json"
PROVENANCE = "provenance.json"
DOCUMENTS_DIR = "documents"  # the document set, unchanged, as `k2gen docset` writes it
RELEASE_COMPOSE = "compose/docker-compose.yml"  # the set's COMPOSE, copied by the compiler (S08)
VIEWS = "compiled/trino/views.json"  # {"views": [View fields]} (S15)
STORAGE_PLAN = "compiled/lake/storage-plan.json"  # storage-plan.schema.json (S15)
RETENTION = "compiled/lake/retention.json"  # {"<warehouse>.<namespace>.<table>": days}: the one retention source (S15)
CONTRACT_SCHEMAS = "compiled/lake/contract_schemas.json"  # the contract's columns per table (S15)
BACKUP_SET = "compiled/lake/backup-set.json"  # backup-set.schema.json (S15)
DAGS = "compiled/airflow/dags.json"  # dags.schema.json (S16)
ONESHOTS = "compiled/oneshots.json"  # oneshots.schema.json (S16)
API_PRINCIPALS = "compiled/api/principals.json"  # {"principals": [id]}: whom k2-api may act as (S14)
CATALOG_INDEX = "compiled/catalog/index.json"  # catalog-product.schema.json#/$defs/index (S18)
DdlEngine = Literal["spark", "trino"]


def pipeline_path(product_id: str) -> str:
    """`compiled/products/<product id>/pipeline.json` (pipeline.schema.json, S16)."""
    return f"compiled/products/{product_id}/pipeline.json"


def catalog_path(product_id: str) -> str:
    """`compiled/catalog/products/<product id>.json` (catalog-product.schema.json, S18)."""
    return f"compiled/catalog/products/{product_id}.json"


def view_sql_path(relation: str) -> str:
    """`compiled/trino/views/<domain>.<fence>.<table>.sql`: a view's SQL, nothing else (S15)."""
    return f"compiled/trino/views/{relation}.sql"


def ddl_path(engine: DdlEngine, warehouse: str, namespace: str, table: str) -> str:
    """`compiled/lake/ddl/<engine>/<warehouse>.<namespace>.<table>.sql`: one CREATE TABLE, emitted
    by S15 and applied by deploy (S19) when `needs.ddl` names `<warehouse>.<namespace>.<table>`.
    spark: tables in warehouse k2 (landing_files, audit_checks, landing, bronze_*), whose
    Trino catalog is read-only; trino: platform tables, through `<domain>_build` (the run
    records, copied from RUN_RECORD_SQL). dbt builds its own tables; views are VIEWS."""
    return f"compiled/lake/ddl/{engine}/{warehouse}.{namespace}.{table}.sql"


# What a change to a release file needs at deploy (manifest `needs`); the longest prefix wins,
# and a path no prefix covers is a compile error, so a new kind of file is classified first.
#   recreate: the compose services whose definition differs (S08 compares them one by one)
#   trinoRestart: Trino reads etc/ at start
#   dagShapeChange: only when a DAG's task list differs (S08 compares the two files' tasks)
#   ddl: the table each added or changed file names (ddl_path)
#   None: read through the pointer at use (rules and groups every 5 s, DAGs every 30 s,
#   pipelines, dbt, views, catalog, one-shots at their next run)
NEEDS_BY_PREFIX: Mapping[str, str | None] = {
    RELEASE_COMPOSE: "recreate",
    "compiled/trino/etc/": "trinoRestart",
    DAGS: "dagShapeChange",
    "compiled/lake/ddl/": "ddl",
    "compiled/": None,
    f"{DOCUMENTS_DIR}/": None,
    PROVENANCE: None,
}


def need_of(path: str) -> str | None:
    """The `needs` field a change to the release file at `path` can set, or None; KeyError
    for a path NEEDS_BY_PREFIX does not cover."""
    found = [p for p in NEEDS_BY_PREFIX if path == p or (p.endswith("/") and path.startswith(p))]
    if not found:
        raise KeyError(f"{path}: no entry of model.NEEDS_BY_PREFIX covers it")
    return NEEDS_BY_PREFIX[max(found, key=len)]


# --- Local secrets (A10; the orchestrator's ruling on Q4) --------------------------------
#
# Every credential is generated on the host into ${K2_SECRETS:-$HOME/k2-secrets}, outside any
# checkout, and never committed, compiled or hashed into a release. A container mounts only
# the files it reads, read-only, under SECRETS_MOUNT. A file refreshed while its reader runs
# (password.db, keys.json, the webhook URL) is mounted by its directory and replaced by a
# rename inside it, because a file-level bind mount pins the inode (CLAUDE.md); the others
# are read at start, mounted as files, and a rotation recreates their readers.

SECRETS_ENV = "K2_SECRETS"
SECRETS_MOUNT = "/run/k2-secrets"


@dataclass(frozen=True)
class SecretFile:
    """One file under ${K2_SECRETS}. `<principal>` and `<canary>` in a path are placeholders."""

    path: str  # relative to ${K2_SECRETS}
    content: str
    mount: str | None  # the read-only path in each container that mounts it; None: never mounted
    services: tuple[str, ...]  # the compose services that mount it
    writer: str  # the step, and the command, that writes it


SECRET_FILES: tuple[SecretFile, ...] = (
    SecretFile("trino/password.db", "Trino's file authenticator file: one `<user>:<bcrypt, cost 10>` line per principal with a password, the release canary's included",
               f"{SECRETS_MOUNT}/trino/password.db", ("trino",),
               "S14 `scripts/principal-secret.py` (`make password`); S19 `scripts/deploy.py` adds each release's canary line"),
    SecretFile("api/keys.json", 'k2-api\'s key map, `{"<sha256 hex of the key>": "<principal id>"}`',
               f"{SECRETS_MOUNT}/api/keys.json", ("k2-api",), "S14 `scripts/principal-secret.py` (`make api-key`)"),
    SecretFile("principals/<principal>.password", "one service principal's password, mode 600",
               f"{SECRETS_MOUNT}/<role>.password", ("dbt-runner", "k2-api", "tools"),
               "S14 `scripts/principal-secret.py` (`make password`)"),
    SecretFile("tls/ca.pem", "the local CA's certificate", f"{SECRETS_MOUNT}/tls/ca.pem",
               ("tools", "dbt-runner", "k2-api"), "S13 `scripts/tls.sh` (`make tls`)"),
    SecretFile("tls/ca.key", "the local CA's private key", None, (), "S13 `scripts/tls.sh` (`make tls`)"),
    SecretFile("tls/trino.pem", "Trino's server key and certificate in one PEM file (`http-server.https.keystore.path`)",
               f"{SECRETS_MOUNT}/tls/trino.pem", ("trino",), "S13 `scripts/tls.sh` (`make tls`)"),
    SecretFile("alertmanager/slack-webhook-url", "the Slack webhook URL, Alertmanager's `api_url_file`",
               f"{SECRETS_MOUNT}/slack-webhook-url", ("alertmanager",), "S09 `make alerts-install-webhook`"),
    SecretFile("canary/<canary>.password", "the release canary's password, read by release-verify on the host", None, (),
               "S19 `scripts/deploy.py`, with its password.db line"),
)


def password_file(principal: str) -> str:
    """`principals/<id>.password` under ${K2_SECRETS}."""
    return f"principals/{principal}.password"


def password_mount(principal: str) -> str:
    """Where a container finds that file: `builder` for dbt_*, `publisher` for pub_*, `<name>`
    for svc.<name>; dbt-runner gets builder, publisher and runs for one run (A7)."""
    if principal.startswith("dbt_"):
        role = "builder"
    elif principal.startswith("pub_"):
        role = "publisher"
    elif principal.startswith("svc."):
        role = principal.removeprefix("svc.")
    else:
        raise ValueError(f"{principal}: only service principals have a password file")
    return f"{SECRETS_MOUNT}/{role}.password"


# --- Names (A5, A8) --------------------------------------------------------------------

LAKE = "lake"  # Trino's read-only catalog over warehouse k2
LAKE_WAREHOUSE = "k2"  # landings and bronze
LANDING_BUCKET = "k2-landing"
LANDING_FILES = "landing_files"  # lake.<fence>.landing_files: one row per landed object
AUDIT_CHECKS = "audit_checks"  # lake.<fence>.audit_checks: the bronze audit's results
KAFKA_LANDING = "landing"  # lake.<fence>.landing: a kafka connector's records
RESERVED_TABLE_NAMES = frozenset({LANDING_FILES, AUDIT_CHECKS, KAFKA_LANDING})
LINEAGE_COLUMN = "source_file_sha256"  # every fetch-kind bronze table's landed-object sha256 (S05)
AUDIT_BRANCH = "audit"  # bronze write-audit-publish branch (Spark)
AUDITED_TAG = "audited"  # the tested-data marker on dbt and code outputs (ADR-061)
BASE_SUFFIX = "__base"  # <fence>__base: the namespace a build writes into (ADR-061)
SNAPSHOT_SUMMARY_KEYS = ("k2.run-id", "k2.release", "k2.batch-id", "k2.source-sha256")
DBT_PROJECT = "k2"  # the compiled dbt project's name (S17), so node ids are model.k2.<name>
RUN_RECORD_SQL = REPO / "platform" / "runs" / "run_record.sql"  # S15 emits it as ddl_path("trino", ...)

# Principals matched by pattern in Trino's rules and resource groups (A8, ADR-061, the gate):
# - pipeline principals run in resource group `pipeline` and alone may set STATISTICS_PROPERTY,
#   by statement or by session header;
# - build-catalog principals alone reach a <domain>_build catalog;
# - READER_CATALOG_ALL get catalog access `all` on every reader catalog, which Trino's
#   definer-view owner check demands; the read-only connector still refuses them every write;
# - READER_NAMESPACE_OWNERS alone hold a privilege in a dbt or code product's reader namespace,
#   through <domain>_build: svc.platform holds none (gate h1.33);
# - RETENTION_PROPERTY_PRINCIPALS alone may set RETENTION_PROPERTIES;
# - REFS_READERS alone read `t$refs`; svc.api reads it and no other metadata of a base.
PIPELINE_PRINCIPALS = (r"dbt_.*", r"pub_.*", r"svc\.runs", r"svc\.platform", r"svc\.catalog")
BUILD_CATALOG_PRINCIPALS = (r"dbt_.*", r"pub_.*", r"svc\.runs", r"svc\.platform")
READER_CATALOG_ALL = (r"pub_.*",)
READER_NAMESPACE_OWNERS = (r"pub_.*",)
STATISTICS_PROPERTY = "statistics_enabled"
RETENTION_PROPERTIES = ("expire_snapshots_min_retention", "remove_orphan_files_min_retention")
RETENTION_PROPERTY_PRINCIPALS = (r"svc\.platform",)
REFS_READERS = ("svc.runs", "svc.catalog", "svc.platform", "svc.api")

MAX_SLUG = 40  # so <fence>__base, dbt_<fence>, pub_<fence> and <domain>_build stay within MAX_IDENTIFIER
MAX_IDENTIFIER = 63  # Postgres's identifier limit, which Lakekeeper's catalog stores names under
SLUG = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")
# A table name holds no `__`: it is the separator of <fence>__base, <fence>__<role> and dbt_model.
TABLE_NAME = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)*")
# Trino 484's reserved words, lower case: the output of
# `java -cp '/usr/lib/trino/lib/*' io.trino.sql.ReservedIdentifiers` in trinodb/trino:484.
# No name may be one (k2-v2.schema.json#/$defs/identifier); unquoted, `current_user` is a function.
TRINO_RESERVED = frozenset("""
    alter and as auto between by case cast constraint create cross cube current_catalog current_date
    current_path current_role current_schema current_time current_timestamp current_user deallocate
    delete describe distinct drop else end escape except exists extract false for from full group
    grouping having in inner insert intersect into is join json_array json_exists json_object
    json_query json_serialize json_table json_value left like listagg localtime localtimestamp
    natural normalize not null on or order outer overlaps prepare recursive right rollup select skip
    table then trim true uescape union unnest using values when where with""".split())
# A column's physicalType: Trino's own spelling (typeof) of an Iceberg-backed type. One level of
# array or map; decimal precision 1 to 38; timestamps and times at microseconds, Iceberg's.
_SCALAR = (r"boolean|integer|bigint|real|double|decimal\((?:[1-9]|[12][0-9]|3[0-8]),(?:[0-9]|[12][0-9]|3[0-8])\)"
           r"|varchar|varbinary|date|time\(6\)|timestamp\(6\)|timestamp\(6\) with time zone|uuid")
PHYSICAL_TYPE = rf"^(?:{_SCALAR}|array\((?:{_SCALAR})\)|map\((?:{_SCALAR}), (?:{_SCALAR})\))$"
# A rowScope value is data in a SQL filter: quoted by sql_literal, and held to this as well.
ROW_SCOPE_VALUE = r"^[A-Za-z0-9][A-Za-z0-9 ._:/-]*$"
# A connector's url, path, key and params may hold only the run's date, {{ds}}: Airflow renders
# what it is given, so any other macro (a variable, a connection) could read a secret.
TEMPLATE = r"^(?:[^{}]|\{\{ds\}\})*$"

# A product's fence names lake namespaces; these are the old layer namespaces and the
# platform's own (A5).
RESERVED_PRODUCT_NAMES = frozenset({"raw", "bronze", "silver", "gold", "audit", "scratch", "dq", "runs"})
# A domain's fence is its reader catalog and warehouse, and <fence>_build its writer
# catalog; its name is also a directory beside contracts/access/ and contracts/schema/.
RESERVED_DOMAIN_NAMES: Mapping[str, str] = {
    "lake": "Trino's read-only catalog over the lake",
    "system": "Trino's own catalog",
    "k2": "the lake's warehouse",
    "information_schema": "the schema every Trino catalog has",
    "access": "the directory of the shared access documents, contracts/access/",
    "schema": "the directory of the schemas, contracts/schema/",
    **{w: "a Trino reserved word" for w in ("table", "select", "catalog", "default", "user")},
}


def fence(name: str) -> str:
    """A domain or product name as a SQL identifier: `station-readings` -> `station_readings`."""
    return name.replace("-", "_")


def product_name_problem(name: str) -> str | None:
    """Why `name` cannot name a product, or None. Names are unique platform-wide (S05)."""
    if not SLUG.fullmatch(name) or len(name) > MAX_SLUG:
        return f"must be lower-case words joined by single hyphens, at most {MAX_SLUG} characters, so its fence holds no __"
    if fence(name) in RESERVED_PRODUCT_NAMES:
        return f"{fence(name)} is reserved: a lake namespace of the old layout or of the platform"
    return None


def domain_name_problem(name: str) -> str | None:
    """Why `name` cannot name a domain, or None."""
    if not SLUG.fullmatch(name) or len(name) > MAX_SLUG:
        return f"must be lower-case words joined by single hyphens, at most {MAX_SLUG} characters, so its fence holds no __"
    if fence(name).endswith("_build"):
        return f"{fence(name)} would be the writer catalog of domain {name[: -len('-build')]}"
    if fence(name) in RESERVED_DOMAIN_NAMES:
        return f"{fence(name)} is {RESERVED_DOMAIN_NAMES[fence(name)]}"
    return None


def physical_type_problem(physical_type: str) -> str | None:
    """Why a column's physicalType is not one K2 takes, or None: PHYSICAL_TYPE, and a decimal's
    scale at most its precision. The schema holds the grammar; this adds the scale."""
    if not re.fullmatch(PHYSICAL_TYPE, physical_type):
        return f"{physical_type!r} is not a Trino type K2 takes, spelt as Trino's typeof spells it"
    for precision, scale in re.findall(r"decimal\(([0-9]+),([0-9]+)\)", physical_type):
        if int(scale) > int(precision):
            return f"{physical_type!r}: a decimal's scale exceeds its precision"
    return None


def product_id(domain: str, product: str) -> str:
    """`<domain>.<product>`: ODPS id, run records, catalog."""
    return f"{domain}.{product}"


def contract_id(domain: str, product: str, table: str) -> str:
    """`<domain>.<product>.<table>`: ODCS id, ports, relationships."""
    return f"{domain}.{product}.{table}"


def reader_catalog(domain: str) -> str:
    """The domain's read-only Trino catalog, statistics off (A8)."""
    return fence(domain)


def build_catalog(domain: str) -> str:
    """The domain's writer catalog; only BUILD_CATALOG_PRINCIPALS reach it (A8)."""
    return f"{fence(domain)}_build"


def warehouse(domain: str) -> str:
    """The domain's Lakekeeper warehouse (ADR-044 layout, kept by ADR-059)."""
    return fence(domain)


def reader_namespace(product: str) -> str:
    """`<fence>`: the product's reader views, or a platform product's tables."""
    return fence(product)


def base_namespace(product: str) -> str:
    """`<fence>__base`: the tables dbt or code builds; never named by a reader rule."""
    return fence(product) + BASE_SUFFIX


def builder(product: str) -> str:
    """`dbt_<fence>`: owns the base namespace in `_build`, holds no GRANT_SELECT (A8)."""
    return f"dbt_{fence(product)}"


def publisher(product: str) -> str:
    """`pub_<fence>`: owns the reader namespace in `_build`, holds SELECT and GRANT_SELECT on its
    base through the reader catalog, moves the tag (ADR-061 part 2)."""
    return f"pub_{fence(product)}"


def group(product: str, role: str) -> str:
    """`<fence>__<role>`: the file group provider's group for one role on one product."""
    return f"{fence(product)}__{role}"


def dag_id(product: str) -> str:
    """The product's Airflow DAG id, also its dbt selector."""
    return fence(product)


def dbt_model(product: str, table: str) -> str:
    """`<fence>__<table>`: the table's dbt model name, unique in the one compiled project, with
    `alias: <table>`. Product SQL (`ref`) and unit_tests.yml (`model:`) name models by it; the
    model's file stays models/<table>.sql. Unique because a fence holds no `__` (S05 checks)."""
    return f"{fence(product)}__{table}"


def dbt_docs(product: str, table: str) -> str:
    """The table's page on the dbt docs site, relative to the site root (catalog `dbtDocs`)."""
    return f"dbt-docs/#!/model/model.{DBT_PROJECT}.{dbt_model(product, table)}"


_CONFIG = re.compile(r"\{\{-?\s*config\((.*?)\)\s*-?\}\}", re.S)
_JINJA_CONSTANTS = {"true": True, "false": False, "none": None}


def model_config(sql: str) -> dict[str, Any]:
    """The keyword arguments of a product model's `{{ config(...) }}`, literals only; {} when it
    has none. The one reader of it: S05 takes `materialized` and `unique_key`, S07 refuses
    `incremental_predicates`. A positional argument, a second config() or a non-literal value
    (a var(), an env_var()) is a ValueError, which a check reports as a finding."""
    calls = _CONFIG.findall(sql)
    if len(calls) > 1:
        raise ValueError("a model has one config() call")
    if not calls:
        return {}
    call = ast.parse(f"f({calls[0]})", mode="eval").body
    if not isinstance(call, ast.Call) or call.args or any(k.arg is None for k in call.keywords):
        raise ValueError("config() takes keyword arguments only")

    def literal(node: ast.expr) -> Any:
        if isinstance(node, ast.Name) and node.id.lower() in _JINJA_CONSTANTS:
            return _JINJA_CONSTANTS[node.id.lower()]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [literal(e) for e in node.elts]
        try:
            return ast.literal_eval(node)
        except ValueError:
            raise ValueError(f"config() value {ast.unparse(node)} is not a literal") from None

    return {str(k.arg): literal(k.value) for k in call.keywords}


def view_relation(domain: str, product: str, table: str) -> str:
    """What readers query: `<domain>.<fence>.<table>`, a view for a dbt or code output, the table
    itself for a platform output."""
    return f"{reader_catalog(domain)}.{reader_namespace(product)}.{table}"


def view_build_relation(domain: str, product: str, table: str) -> str:
    """Where the publisher creates that view: `<domain>_build.<fence>.<table>` (gate h1.01)."""
    return f"{build_catalog(domain)}.{reader_namespace(product)}.{table}"


def base_relation(domain: str, product: str, table: str) -> str:
    """A built table through the reader catalog, `<domain>.<fence>__base.<table>`: what the view
    and other products' inputs read at the tag."""
    return f"{reader_catalog(domain)}.{base_namespace(product)}.{table}"


def build_relation(domain: str, product: str, table: str) -> str:
    """Where dbt writes: `<domain>_build.<fence>__base.<table>`."""
    return f"{build_catalog(domain)}.{base_namespace(product)}.{table}"


def lake_relation(product: str, table: str) -> str:
    """A connector's table in warehouse k2: `lake.<fence>.<table>` (A5)."""
    return f"{LAKE}.{fence(product)}.{table}"


def landing_prefix(domain: str, product: str) -> str:
    """The product's prefix in the landing bucket, `<domain>/<product>/`."""
    return f"{domain}/{product}/"


def landing_key(domain: str, product: str, day: str, sha256: str, ext: str) -> str:
    """`s3://k2-landing/<domain>/<product>/<YYYY-MM-DD>/<sha256>.<ext>`, written by conditional PUT."""
    return f"s3://{LANDING_BUCKET}/{landing_prefix(domain, product)}{day}/{sha256}.{ext}"


def set_sha256(documents: Mapping[str, bytes]) -> str:
    """The hash of a set of documents: sha256 of `<sha256>  <path>\\n` per document, sorted
    by path, exactly as `sha256sum` prints them. provenance.json's setSha256 and each
    manifest product's sha256."""
    lines = "".join(f"{hashlib.sha256(documents[p]).hexdigest()}  {p}\n" for p in sorted(documents))
    return hashlib.sha256(lines.encode()).hexdigest()


def release_id(tag: str, files: Mapping[str, str], images: Mapping[str, str]) -> str:
    """`<tag>-<sha12>`, sha12 from the canonical JSON (sorted keys, no spaces) of
    {files, images}: one id for one document set and one image set (A1)."""
    canonical = json.dumps({"files": dict(files), "images": dict(images)}, sort_keys=True, separators=(",", ":"))
    return f"{tag}-{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"


def canary_user(set_sha: str) -> str:
    """`k2_release_<setSha256[:12]>`: the user only a release's rules admit, which release-verify
    logs in as; the one spelling. Its sha12 is the document set's, since the release id hashes
    rules.json itself. Not a declared principal: S12's rules and S19's password.db line make it."""
    return f"k2_release_{set_sha[:12]}"


def canary_password_file(set_sha: str) -> str:
    """`canary/<canary user>.password` under ${K2_SECRETS} (S19)."""
    return f"canary/{canary_user(set_sha)}.password"


# --- Quoting: every emitter writes SQL names and values through these -------------------


def quote_ident(name: str) -> str:
    """A Trino identifier, always double-quoted, so no name is ever read as a keyword or a
    function (`current_user`). Names are schema-checked first; this is the second line."""
    if not name or any(c < " " for c in name):
        raise ValueError(f"{name!r} cannot be an identifier")
    return '"' + name.replace('"', '""') + '"'


def quote_relation(relation: str) -> str:
    """`a.b.c` from the naming helpers as `"a"."b"."c"`."""
    return ".".join(quote_ident(part) for part in relation.split("."))


def sql_literal(value: str | int) -> str:
    """A SQL literal for a policy value: an integer as digits, a string single-quoted with every
    quote doubled, so a value is always one token."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{value!r}: only strings and integers are policy values")
    if isinstance(value, int):
        return str(value)
    if any(c < " " for c in value):
        raise ValueError(f"{value!r}: a control character in a SQL literal")
    return "'" + value.replace("'", "''") + "'"


# --- Parsing: the one YAML loader of every v2 document ----------------------------------


class DocumentError(ValueError):
    """A document k2gen refuses to read: `<path>: line <n>: <why>`."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses what makes one text read two ways: a duplicate key (safe_load
    keeps the last, so a second `mask:` silently wins), an anchor or alias, a merge key."""

    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node | None:
        event = self.peek_event()  # type: ignore[no-untyped-call]
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise DocumentError(f"line {event.start_mark.line + 1}: an anchor or alias; write the value out")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise DocumentError(f"line {key_node.start_mark.line + 1}: a merge key (<<); write the keys out")
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
            except TypeError:  # an unhashable key: SafeLoader's own error follows
                continue
            if duplicate:
                raise DocumentError(f"line {key_node.start_mark.line + 1}: key {key!r} appears twice")
            seen.add(key)
        return super().construct_mapping(node, deep)


def parse(path: str, data: bytes) -> Any:
    """Every v2 document (S04, S05, S07 and any later reader) is parsed by this, never by
    yaml.safe_load: one YAML document, no duplicate key, anchor, alias or merge key."""
    loader = _StrictLoader(data)
    try:
        return loader.get_single_data()
    except DocumentError as e:
        raise DocumentError(f"{path}: {e}") from None
    except yaml.YAMLError as e:
        raise DocumentError(f"{path}: {e}") from None
    finally:
        loader.dispose()


# --- Schemas ---------------------------------------------------------------------------


def document_kind(path: str) -> str | None:
    """The kind (a key of SCHEMAS) of a document-set path; None for a file no schema
    covers: models, unit tests, seeds, fixtures, parsers, the compose file, the schemas."""
    if path in (DOMAINS, ROLES, PRINCIPALS, EXPECTATIONS):
        return {DOMAINS: "domains", ROLES: "roles", PRINCIPALS: "principals", EXPECTATIONS: "expectations"}[path]
    parts = path.split("/")
    if len(parts) != 4 or parts[0] != "contracts" or parts[1] in RESERVED_DOMAIN_NAMES:
        return None
    if parts[3] == V2_PRODUCT_FILE:
        return "product"
    if parts[3] == "grants.yaml":
        return "grants"
    return "contract" if parts[3].endswith(".odcs.yaml") else None


@cache
def _registry() -> Registry[Any]:
    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        contents = json.loads(path.read_text())
        if "$id" in contents:  # k2.schema.json, the old profile, has none and is not referenced
            resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


@cache
def _validator(ref: str) -> jsonschema.protocols.Validator:
    name, _, pointer = ref.partition("#")
    contents = json.loads((SCHEMA_DIR / name).read_text())
    if "$id" not in contents:  # a vendored standard, whole and on its own
        return jsonschema.validators.validator_for(contents)(contents)
    wrapper = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": f"{contents['$id']}#{pointer}"}
    return jsonschema.Draft202012Validator(wrapper, registry=_registry())


def schema_errors(kind: str, doc: Any) -> list[str]:
    """Every schema error of a parsed document of `kind`, one line each: `<schema>: <json path>: <message>`."""
    errors = []
    for ref in SCHEMAS[kind]:
        found = sorted(_validator(ref).iter_errors(doc), key=lambda e: e.json_path)
        errors += [f"{ref.partition('#')[0]}: {e.json_path}: {e.message}" for e in found]
    return errors


# --- The documents (A2, A10) -----------------------------------------------------------

Status = Literal["active", "paused", "deprecated", "retired"]
ProductType = Literal["sourceAligned", "aggregate", "consumerAligned"]
Layer = Literal["bronze", "silver", "gold", "mart"]
# serving: a table only a serving engine holds (an output port's `serves`), no lake table;
# every lake emitter, the rules and the relation helpers skip it (LAKE_BUILDS).
BuildKind = Literal["connector", "dbt", "platform", "code", "serving"]
LAKE_BUILDS: frozenset[str] = frozenset({"connector", "dbt", "platform", "code"})
Backup = Literal["restore", "rebuild"]
TimeMeaning = Literal["event", "publication", "valid", "known"]
AsOf = Literal["valid", "known"]
ConnectorKind = Literal["kafka", "public-api", "https-file", "s3-file", "sftp", "ftp"]
PrincipalKind = Literal["person", "service"]
PolicyKind = Literal["delay", "mask", "rowScope"]
Severity = Literal["error", "warning"]
Ref = Literal["main", "audited"]


@dataclass(frozen=True)
class Domain:
    """One entry of contracts/domains.yaml."""

    name: str
    owner: str
    kind: Literal["source-aligned", "consumer-aligned"]
    description: str


@dataclass(frozen=True)
class Connector:
    """The product's `connector` property: its kind, and every other field as the document has it."""

    kind: ConnectorKind
    settings: Mapping[str, Any]


@dataclass(frozen=True)
class Schedule:
    cron: str
    timeout_minutes: int | None


@dataclass(frozen=True)
class Terms:
    redistribution: Literal["allowed", "forbidden"]
    attribution: str | None


@dataclass(frozen=True)
class LifecycleChange:
    since: str  # YYYY-MM-DD
    reason: str
    decision: str  # ADR-NNN
    successor: str | None  # a product id
    sunset: str | None  # YYYY-MM-DD
    resume: str | None


@dataclass(frozen=True)
class Example:
    description: str
    sql: str


@dataclass(frozen=True)
class Serves:
    """A serving copy outside the lake (an output port's `serves`)."""

    engine: Literal["clickhouse"]
    profile: str
    status: Literal["active", "paused"]


@dataclass(frozen=True)
class InputPort:
    name: str
    version: str
    contract_id: str
    required: bool  # the port's `required` property; true when absent
    max_lag_minutes: int | None


@dataclass(frozen=True)
class ContractRef:
    """An output port's inputContracts entry."""

    id: str
    version: str


@dataclass(frozen=True)
class OutputPort:
    name: str
    version: str
    contract_id: str
    input_contracts: tuple[ContractRef, ...]
    serves: Serves | None


@dataclass(frozen=True)
class Relationship:
    to: str  # <domain>/<product>/<table>.odcs.yaml#/schema/<table>/properties/<column>
    as_of: AsOf | None


@dataclass(frozen=True)
class Column:
    name: str
    physical_type: str  # Trino's spelling, PHYSICAL_TYPE
    logical_type: str | None
    pattern: str | None  # ODCS logicalTypeOptions.pattern: every value matches it (bronze audit, A4)
    description: str | None  # None fails S05's every-column-described check
    required: bool
    primary_key: bool
    primary_key_position: int | None
    unique: bool
    classification: str | None
    unit: str | None
    time: TimeMeaning | None
    relationships: tuple[Relationship, ...]


@dataclass(frozen=True)
class Delay:
    column: str
    minutes: int


@dataclass(frozen=True)
class RowScope:
    column: str
    values: tuple[str | int, ...]


@dataclass(frozen=True)
class RolePolicy:
    """What one role sees of one table; each part None or empty when not set."""

    delay: Delay | None
    mask: tuple[str, ...]
    row_scope: RowScope | None


@dataclass(frozen=True)
class TableRole:
    """One entry of the contract's ODCS `roles`."""

    role: str
    first_level_approvers: str | None


@dataclass(frozen=True)
class Quality:
    """One ODCS quality entry of the table."""

    name: str | None
    type: str
    engine: str | None  # dbt or k2-connector for type custom
    description: str | None
    implementation: Any


@dataclass(frozen=True)
class Sla:
    """One ODCS slaProperties entry."""

    property: str
    value: Any
    unit: str | None
    element: str | None


@dataclass(frozen=True)
class Build:
    kind: BuildKind
    transform: str | None  # <name>@<major> when kind is code


@dataclass(frozen=True)
class Table:
    """One `<table>.odcs.yaml`."""

    path: str  # in the document set
    contract_id: str
    name: str
    version: str
    physical_name: str  # <fence>.<table>; for build serving, the serving engine's <database>.<table>
    purpose: str
    owner: str
    layer: Layer
    build: Build
    backup: Backup
    retention: int | None  # days; None takes the 7-day floor; emitted once, in RETENTION
    partitioning: tuple[str, ...]
    sort: tuple[str, ...]
    columns: tuple[Column, ...]
    roles: tuple[TableRole, ...]
    policies: Mapping[str, RolePolicy]  # role -> policy; a role absent here sees everything
    quality: tuple[Quality, ...]
    sla: tuple[Sla, ...]


def _lake(t: Table) -> tuple[str, str, str, BuildKind]:
    if t.build.kind not in LAKE_BUILDS:
        raise ValueError(f"{t.contract_id}: build {t.build.kind} has no lake table; skip it (model.LAKE_BUILDS)")
    domain, product, table = t.contract_id.split(".")
    return domain, product, table, t.build.kind


def reader_relation(t: Table) -> str:
    """What a reader queries, by build kind: connector `lake.<fence>.<table>`; dbt and code the
    view `<domain>.<fence>.<table>`; platform the table `<domain>.<fence>.<table>`. The catalog,
    the rules, the expectations and the REST data route name tables by it."""
    domain, product, table, kind = _lake(t)
    return lake_relation(product, table) if kind == "connector" else view_relation(domain, product, table)


def input_relation(t: Table) -> str:
    """What another product's builder reads when `t` is its input (pipeline and run-record
    `inputs[].relation`): connector `lake.<fence>.<table>`; dbt and code the base
    `<domain>.<fence>__base.<table>` through the reader catalog; platform the table itself.
    Always read at tested_ref(t), pinned by k2_input."""
    domain, product, table, kind = _lake(t)
    if kind == "connector":
        return lake_relation(product, table)
    return base_relation(domain, product, table) if kind in ("dbt", "code") else view_relation(domain, product, table)


def tested_ref(t: Table) -> Ref:
    """The ref whose snapshot is the tested one: `audited` for dbt and code outputs (the tag the
    publisher moves), `main` for connector tables (bronze moves only after its audit) and
    platform tables. The wrapper, k2-api and the catalog read it from `t$refs` (A6)."""
    return "audited" if _lake(t)[3] in ("dbt", "code") else "main"


@dataclass(frozen=True)
class Grant:
    principal: str
    role: str


@dataclass(frozen=True)
class Product:
    """One product directory, contracts/<domain>/<name>/."""

    path: str  # the directory in the document set, no trailing slash
    id: str
    name: str
    domain: str
    version: str
    status: Status
    type: ProductType
    owner: str
    purpose: str
    limitations: str | None
    usage: str | None
    licence: str | None  # the authoritativeDefinitions entry of type licence
    tags: tuple[str, ...]
    input_ports: tuple[InputPort, ...]
    output_ports: tuple[OutputPort, ...]
    connector: Connector | None
    schedule: Schedule | None
    terms: Terms | None
    lifecycle_change: LifecycleChange | None
    examples: tuple[Example, ...]
    tables: tuple[Table, ...]
    grants: tuple[Grant, ...]
    models: Mapping[str, str]  # path under models/ -> SQL
    unit_tests: str | None  # unit_tests.yml as written
    seeds: Mapping[str, bytes]  # path under seeds/ -> bytes, each declared by the `seeds` property


@dataclass(frozen=True)
class RoleDef:
    """One entry of contracts/access/roles.yaml."""

    name: str
    description: str
    requires: tuple[PolicyKind, ...]


@dataclass(frozen=True)
class Principal:
    """One entry of contracts/access/principals.yaml. No credential, no hash."""

    id: str
    kind: PrincipalKind
    owner: str
    description: str | None


@dataclass(frozen=True)
class TableExpectation:
    principal: str
    table: str
    via: tuple[Literal["sql", "rest"], ...]
    visible: tuple[str, ...]
    masked: tuple[str, ...]
    min_row_age_minutes: int | None
    must_not_appear: str | None


@dataclass(frozen=True)
class DeniedExpectation:
    principal: str
    table: str
    via: tuple[Literal["sql", "rest"], ...]


@dataclass(frozen=True)
class StatementExpectation:
    principal: str
    sql: str
    expect: Literal["denied", "error", "noBounds"]
    session: Mapping[str, str]  # sent as X-Trino-Session headers with the statement; empty for none


@dataclass(frozen=True)
class ProbeExpectation:
    origin: Literal["host", "k2-net", "k2-catalog", "fetch", "tools"]  # the row's probe.from
    target: str
    expect: Literal["refused", "open"]


@dataclass(frozen=True)
class DriftExpectation:
    drift: Literal["catalog"]
    expect: Literal["clean"]


Expectation = TableExpectation | DeniedExpectation | StatementExpectation | ProbeExpectation | DriftExpectation


@dataclass(frozen=True)
class DocumentProvenance:
    path: str
    sha256: str
    last_commit: str
    pr: int | None


@dataclass(frozen=True)
class Provenance:
    """provenance.json (A2)."""

    source: Literal["git", "registry"]
    tag: str
    commit: str
    tagger: str
    tagged_at: str
    set_sha256: str
    documents: tuple[DocumentProvenance, ...]


@dataclass(frozen=True)
class DocumentSet:
    """The compiler's only input: every document's bytes by path, and where they came from."""

    documents: Mapping[str, bytes]
    provenance: Provenance


@dataclass(frozen=True)
class Model:
    """A document set, parsed and schema-valid: what every emitter reads."""

    domains: tuple[Domain, ...]
    products: tuple[Product, ...]
    roles: tuple[RoleDef, ...]
    principals: tuple[Principal, ...]
    expectations: tuple[Expectation, ...]
    provenance: Provenance
    images: Mapping[str, str]  # service -> image ref: k2gen release's --images; empty for k2gen check


@dataclass(frozen=True)
class Finding:
    """One result of a check. `code` is `<check module>.<kebab-case>`, e.g. `docs.column-undescribed`."""

    code: str
    severity: Severity
    path: str  # the document's path in the set
    message: str


@dataclass(frozen=True)
class ManifestProduct:
    id: str
    version: str
    status: Status
    sha256: str


@dataclass(frozen=True)
class Needs:
    recreate: tuple[str, ...]
    trino_restart: bool
    dag_shape_change: bool
    ddl: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    """manifest.json (A1, contracts/schema/manifest.schema.json). No secret and no checksum of
    one: deploy records those in its own deploy record (DEPLOY_LOG)."""

    release: str
    tag: str
    commit: str
    built_at: str
    k2gen: str
    previous: str | None
    set_sha256: str
    products: tuple[ManifestProduct, ...]
    files: Mapping[str, str]
    images: Mapping[str, str]
    needs: Needs


@dataclass(frozen=True)
class Release:
    """A sealed release directory, <releases root>/<id>/, as k2gen.release.read opens it."""

    id: str
    root: Path
    manifest: Manifest


@dataclass(frozen=True)
class View:
    """One reader view, as VIEWS lists it: S15 writes it, the drift check (S20) and the dbt
    wrapper (phase 002) read it. Its SQL is exactly `SELECT <columns> FROM <base> FOR
    VERSION AS OF 'audited'`, every name through quote_ident (ADR-061 part 1)."""

    relation: str  # view_relation(): the reader name
    base: str  # base_relation(): read through the reader catalog
    columns: tuple[str, ...]  # the contract's columns in order: C of ADR-061 part 5
    owner: str  # publisher()


# --- The platform's own tables in a connector product's lake namespace (A5) --------------
#
# S15 emits each one's DDL, ddl_path("spark", "k2", <fence>, <name>), from these; phase 002
# writes them. Types in Trino's spelling (PHYSICAL_TYPE), as the contracts' are.


@dataclass(frozen=True)
class PlatformColumn:
    name: str
    type: str
    comment: str


@dataclass(frozen=True)
class PlatformTable:
    name: str
    columns: tuple[PlatformColumn, ...]
    partitioning: tuple[str, ...]


_RUN = (PlatformColumn("run_id", "varchar", "The Airflow run, <dag_id>/<run_id>"),
        PlatformColumn("release_id", "varchar", "The release the run resolved at its start (A1)"))
PLATFORM_TABLES: Mapping[str, PlatformTable] = {
    LANDING_FILES: PlatformTable(LANDING_FILES, (
        PlatformColumn("object_key", "varchar", "The landed object, s3://k2-landing/<domain>/<product>/<YYYY-MM-DD>/<sha256>.<ext>"),
        PlatformColumn("sha256", "varchar", "The bytes' sha256, as fetch.json recorded it and the Spark task recomputed it"),
        PlatformColumn("bytes", "bigint", "The object's size in bytes"),
        PlatformColumn("url", "varchar", "The URL fetched, {{ds}} resolved; never a credential"),
        PlatformColumn("final_url", "varchar", "The URL after redirects"),
        PlatformColumn("http_status", "integer", "The final response's HTTP status"),
        PlatformColumn("fetched_at", "timestamp(6) with time zone", "When the fetch received the bytes, UTC"),
        PlatformColumn("landed_at", "timestamp(6) with time zone", "When the conditional write returned, UTC; the partition source"),
        PlatformColumn("parsed", "boolean", "False: the parser failed; the bytes stay and the batch fails at bronze"),
        PlatformColumn("batch_id", "varchar", "The batch, the snapshot summary key k2.batch-id"),
        *_RUN), ("day(landed_at)",)),
    AUDIT_CHECKS: PlatformTable(AUDIT_CHECKS, (
        PlatformColumn("table_name", "varchar", "The bronze table audited, lake.<fence>.bronze_<name>"),
        PlatformColumn("batch_id", "varchar", "The batch audited"),
        PlatformColumn("snapshot_id", "bigint", "The audit branch's snapshot the check ran at; the publish guard requires the branch's current one"),
        PlatformColumn("check_name", "varchar", "A generic check or one of the parser's audit functions"),
        PlatformColumn("passed", "boolean", "Whether the check passed"),
        PlatformColumn("detail", "varchar", "What failed, at most 1000 characters; never a secret"),
        PlatformColumn("checked_at", "timestamp(6) with time zone", "When the check ran, UTC; the partition source"),
        *_RUN), ("day(checked_at)",)),
    KAFKA_LANDING: PlatformTable(KAFKA_LANDING, (
        PlatformColumn("kafka_partition", "integer", "The topic partition"),
        PlatformColumn("kafka_offset", "bigint", "The record's offset; end offsets also go in the snapshot summary (ADR-022)"),
        PlatformColumn("kafka_timestamp", "timestamp(6) with time zone", "The record's timestamp"),
        PlatformColumn("record_key", "varbinary", "The key's bytes as read"),
        PlatformColumn("record_value", "varbinary", "The value's bytes as read"),
        PlatformColumn("batch_id", "varchar", "The batch, the snapshot summary key k2.batch-id"),
        PlatformColumn("landed_at", "timestamp(6) with time zone", "When the batch committed, UTC; the partition source"),
        *_RUN), ("day(landed_at)",)),
}


# --- Loading (S04): private to load() ----------------------------------------------------
#
# Every refusal is one line, `<path>: <why>`, and a schema error's `<path>` carries the JSON
# pointer into the document (`<file>#/schema/0/properties/3/name`), the form a relationship's
# `to` already uses. load() reports every line it finds, then raises once.

_PRODUCT_PARTS = ("models", "seeds", "unit_tests.yml")  # a product's files that are not documents


def _pointer(parts: Any) -> str:
    """RFC 6901: each key or index after a `/`, with `~` and `/` escaped."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _schema_findings(path: str, kind: str, doc: Any) -> list[str]:
    """schema_errors' validation, in its order (the published standard, then K2's profile),
    each error located by JSON pointer."""
    found = []
    for ref in SCHEMAS[kind]:
        for e in sorted(_validator(ref).iter_errors(doc), key=lambda e: e.json_path):
            found.append(f"{path}#{_pointer(e.absolute_path)}: {ref.partition('#')[0]}: {e.message}")
    return found


def _props(entries: Any, where: str, errors: list[str]) -> dict[str, Any]:
    """A `customProperties` list as {property: value}. Each property at most once: the model
    holds one value, and taking either of two would read the document one way of two."""
    props: dict[str, Any] = {}
    for i, entry in enumerate(entries or ()):
        if entry["property"] in props:
            errors.append(f"{where}/{i}: custom property {entry['property']} appears twice")
        props[entry["property"]] = entry["value"]
    return props


def _text(path: str, data: bytes, errors: list[str]) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        errors.append(f"{path}: not UTF-8 text ({e.reason} at byte {e.start})")
        return ""


def _column(c: Mapping[str, Any], where: str, errors: list[str]) -> Column:
    props = _props(c.get("customProperties"), f"{where}/customProperties", errors)
    if "quality" in c:  # ODCS allows it; the model, the emitters and S05 read the table's only
        errors.append(f"{where}/quality: K2 reads quality on the table, schema/0/quality; move it there")
    relationships = tuple(
        Relationship(r["to"], _props(r.get("customProperties"), f"{where}/relationships/{i}/customProperties", errors).get("asOf"))
        for i, r in enumerate(c.get("relationships", ())))
    return Column(
        name=c["name"], physical_type=c["physicalType"], logical_type=c.get("logicalType"),
        pattern=c.get("logicalTypeOptions", {}).get("pattern"), description=c.get("description"),
        required=c.get("required", False), primary_key=c.get("primaryKey", False),
        primary_key_position=c.get("primaryKeyPosition"), unique=c.get("unique", False),
        classification=c.get("classification"), unit=props.get("unit"), time=props.get("time"),
        relationships=relationships)


def _table(path: str, doc: Mapping[str, Any], errors: list[str]) -> Table:
    (schema,) = doc["schema"]  # the profile holds exactly one
    props = _props(doc["customProperties"], f"{path}#/customProperties", errors)
    build = props["build"]
    policies = {
        role: RolePolicy(
            delay=Delay(p["delay"]["column"], p["delay"]["minutes"]) if "delay" in p else None,
            mask=tuple(p.get("mask", ())),
            row_scope=RowScope(p["rowScope"]["column"], tuple(p["rowScope"]["values"])) if "rowScope" in p else None)
        for role, p in props.get("policies", {}).items()}
    return Table(
        path=path, contract_id=doc["id"], name=doc["name"], version=doc["version"],
        physical_name=schema["physicalName"], purpose=doc["description"]["purpose"], owner=doc["team"]["name"],
        layer=props["layer"], build=Build("code", build["code"]) if isinstance(build, dict) else Build(build, None),
        backup=props["backup"], retention=props.get("retention"),
        partitioning=tuple(props.get("partitioning", ())), sort=tuple(props.get("sort", ())),
        columns=tuple(_column(c, f"{path}#/schema/0/properties/{i}", errors) for i, c in enumerate(schema["properties"])),
        roles=tuple(TableRole(r["role"], r.get("firstLevelApprovers")) for r in doc.get("roles", ())),
        policies=policies,
        quality=tuple(Quality(q.get("name"), q.get("type", "library"), q.get("engine"), q.get("description"), q.get("implementation"))
                      for q in schema.get("quality", ())),  # ODCS's default type is library
        sla=tuple(Sla(s["property"], s.get("value"), s.get("unit"), s.get("element")) for s in doc.get("slaProperties", ())))


def _product(pdir: str, parsed: Mapping[str, Any], documents: Mapping[str, bytes], errors: list[str]) -> Product:
    """The product in directory `pdir`: its product file, the contracts and grants beside it,
    models/*.sql, unit_tests.yml as written, and each seed its `seeds` property declares."""
    path = f"{pdir}/{V2_PRODUCT_FILE}"
    doc = parsed[path]
    files = {p.removeprefix(f"{pdir}/"): data for p, data in documents.items() if p.startswith(f"{pdir}/")}
    props = _props(doc.get("customProperties"), f"{path}#/customProperties", errors)
    licences = [d["url"] for d in doc.get("authoritativeDefinitions", ()) if d.get("type") == "licence"]
    if len(licences) > 1:
        errors.append(f"{path}#/authoritativeDefinitions: {len(licences)} entries of type licence; a product has one")
    inputs = []
    for i, port in enumerate(doc.get("inputPorts", ())):
        pp = _props(port.get("customProperties"), f"{path}#/inputPorts/{i}/customProperties", errors)
        inputs.append(InputPort(port["name"], port["version"], port["contractId"], pp.get("required", True), pp.get("maxLagMinutes")))
    outputs = []
    for i, port in enumerate(doc["outputPorts"]):
        serves = _props(port.get("customProperties"), f"{path}#/outputPorts/{i}/customProperties", errors).get("serves")
        outputs.append(OutputPort(
            port["name"], port["version"], port["contractId"],
            tuple(ContractRef(c["id"], c["version"]) for c in port.get("inputContracts", ())),
            Serves(serves["engine"], serves["profile"], serves["status"]) if serves else None))
    declared = {s["path"].removeprefix("seeds/") for s in props.get("seeds", ())}
    present = {f.removeprefix("seeds/") for f in files if f.startswith("seeds/")}
    errors += [f"{path}#/customProperties: declares seed seeds/{s}, which the set does not hold" for s in sorted(declared - present)]
    errors += [f"{pdir}/seeds/{s}: not declared by the product's seeds property" for s in sorted(present - declared)]
    unit_tests = None
    if "unit_tests.yml" in files:
        unit_tests = _text(f"{pdir}/unit_tests.yml", files["unit_tests.yml"], errors)
        try:  # kept as written, but read one way only, as every v2 YAML document is
            parse(f"{pdir}/unit_tests.yml", files["unit_tests.yml"])
        except DocumentError as e:
            errors.append(str(e))
    connector, schedule, terms, change = (props.get(k) for k in ("connector", "schedule", "terms", "lifecycleChange"))
    grants = parsed.get(f"{pdir}/grants.yaml", {"grants": []})["grants"]
    return Product(
        path=pdir, id=doc["id"], name=doc["name"], domain=doc["domain"], version=doc["version"],
        status=doc["status"], type=doc["type"], owner=doc["team"]["name"],
        purpose=doc["description"]["purpose"], limitations=doc["description"].get("limitations"),
        usage=doc["description"].get("usage"), licence=licences[0] if licences else None,
        tags=tuple(doc.get("tags", ())), input_ports=tuple(inputs), output_ports=tuple(outputs),
        connector=Connector(connector["kind"], {k: v for k, v in connector.items() if k != "kind"}) if connector else None,
        schedule=Schedule(schedule["cron"], schedule.get("timeoutMinutes")) if schedule else None,
        terms=Terms(terms["redistribution"], terms.get("attribution")) if terms else None,
        lifecycle_change=LifecycleChange(change["since"], change["reason"], change["decision"], change.get("successor"),
                                         change.get("sunset"), change.get("resume")) if change else None,
        examples=tuple(Example(e["description"], e["sql"]) for e in props.get("examples", ())),
        tables=tuple(_table(p, parsed[p], errors) for p in sorted(parsed) if p.startswith(f"{pdir}/") and document_kind(p) == "contract"),
        grants=tuple(Grant(g["principal"], g["role"]) for g in grants),
        models={f.removeprefix("models/"): _text(f"{pdir}/{f}", data, errors)
                for f, data in sorted(files.items()) if f.startswith("models/") and f.endswith(".sql")},
        unit_tests=unit_tests,
        seeds={s: files[f"seeds/{s}"] for s in sorted(declared & present)})


def _expectation(row: Mapping[str, Any]) -> Expectation:
    """One row, told apart by its keys as the schema tells the five shapes apart."""
    via = tuple(row.get("via", ("sql",)))  # REST joins in phase 003 (expectations.schema.json)
    if "probe" in row:
        return ProbeExpectation(row["probe"]["from"], row["probe"]["target"], row["expect"])
    if "drift" in row:
        return DriftExpectation(row["drift"], row["expect"])
    if "sql" in row:
        return StatementExpectation(row["principal"], row["sql"], row["expect"], dict(row.get("session", {})))
    if row.get("denied"):
        return DeniedExpectation(row["principal"], row["table"], via)
    return TableExpectation(row["principal"], row["table"], via, tuple(row["visible"]), tuple(row.get("masked", ())),
                            row.get("minRowAgeMinutes"), row.get("mustNotAppear"))


def load(docs: DocumentSet, images: Mapping[str, str] | None = None) -> Model:
    """Parse a document set into the model, every document through `parse`, refusing any
    document `schema_errors` rejects."""
    errors: list[str] = []
    parsed: dict[str, Any] = {}
    for path in sorted(docs.documents):
        kind = document_kind(path)
        if kind is None:
            continue
        try:
            doc = parse(path, docs.documents[path])
        except DocumentError as e:
            errors.append(str(e))
            continue
        found = _schema_findings(path, kind, doc)
        errors += found
        if not found:
            parsed[path] = doc
    if errors:  # the model below assumes schema-valid documents
        raise DocumentError("\n".join(errors))

    # A contract, grants, model, seed or unit test belongs to the product file beside it.
    pdirs = sorted(p.removesuffix(f"/{V2_PRODUCT_FILE}") for p in parsed if document_kind(p) == "product")
    for path in sorted(docs.documents):
        parts = path.split("/")
        pdir = "/".join(parts[:3])
        in_domain = len(parts) >= 4 and parts[0] == "contracts" and parts[1] not in RESERVED_DOMAIN_NAMES
        if in_domain and (document_kind(path) in ("contract", "grants") or parts[3] in _PRODUCT_PARTS) and pdir not in pdirs:
            errors.append(f"{path}: {pdir}/ holds no {V2_PRODUCT_FILE}, so it belongs to no product")
    products = tuple(_product(pdir, parsed, docs.documents, errors) for pdir in pdirs)
    domains = parsed.get(DOMAINS, {"domains": {}})["domains"]
    roles = parsed.get(ROLES, {"roles": {}})["roles"]
    model = Model(
        domains=tuple(Domain(name, d["owner"], d["kind"], d["description"]) for name, d in domains.items()),
        products=products,
        roles=tuple(RoleDef(name, r["description"], tuple(r.get("requires", ()))) for name, r in roles.items()),
        principals=tuple(Principal(p["id"], p["kind"], p["owner"], p.get("description"))
                         for p in parsed.get(PRINCIPALS, {"principals": []})["principals"]),
        expectations=tuple(_expectation(r) for r in parsed.get(EXPECTATIONS, {"expectations": []})["expectations"]),
        provenance=docs.provenance,
        images=dict(images or {}),
    )
    if errors:
        raise DocumentError("\n".join(errors))
    return model
