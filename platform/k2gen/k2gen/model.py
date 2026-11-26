"""The v2 document model, its schemas, and the names every step shares (phase 001).

Frozen by the interfaces step S00a: a type, constant or helper here, or a schema in
contracts/schema/, changes only through the orchestrator (platform/k2gen/INTERFACES.md).
Step S04 fills `load`. The naming helpers, `set_sha256`, `release_id` and `schema_errors`
have real bodies now, because every emitter, check and script names things through them.

Places marked `provisional until S01` follow the tested-data design of ADR-061 (a
definer view over the `audited` tag, owned by a publisher) and are finalised when the
gate `spine-h-tested-data-view` reports.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

import jsonschema
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "contracts" / "schema"
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
}

# --- Where documents live (A2) ---------------------------------------------------------

DOMAINS = "contracts/domains.yaml"
ROLES = "contracts/access/roles.yaml"
PRINCIPALS = "contracts/access/principals.yaml"
EXPECTATIONS = "tests/access/expectations.yaml"
# What `git archive <tag>` extracts into a document set. The expectations are in it so the
# coverage check (S07) stays a pure function of the set.
DOCUMENT_ROOTS = ("contracts/", "domains/", EXPECTATIONS)

# --- Release layout (A1) ---------------------------------------------------------------

MANIFEST = "manifest.json"
PROVENANCE = "provenance.json"
DOCUMENTS_DIR = "documents"  # the document set, unchanged, as `k2gen docset` writes it
VIEWS = "compiled/trino/views.json"  # {"views": [View fields]}; provisional until S01

# --- Names (A5, A8) --------------------------------------------------------------------

LAKE = "lake"  # Trino's read-only catalog over warehouse k2
LAKE_WAREHOUSE = "k2"  # landings and bronze
LANDING_BUCKET = "k2-landing"
LANDING_FILES = "landing_files"  # lake.<fence>.landing_files: one row per landed object
AUDIT_CHECKS = "audit_checks"  # lake.<fence>.audit_checks: the bronze audit's results
KAFKA_LANDING = "landing"  # lake.<fence>.landing: a kafka connector's records
RESERVED_TABLE_NAMES = frozenset({LANDING_FILES, AUDIT_CHECKS, KAFKA_LANDING})
AUDIT_BRANCH = "audit"  # bronze write-audit-publish branch (Spark)
AUDITED_TAG = "audited"  # the tested-data marker on dbt outputs; provisional until S01
BASE_SUFFIX = "__base"  # <fence>__base: the namespace dbt builds into; provisional until S01
SNAPSHOT_SUMMARY_KEYS = ("k2.run-id", "k2.release", "k2.batch-id", "k2.source-sha256")

# Principals matched by pattern in Trino's rules and resource groups. Pipeline principals
# run in resource group `pipeline` and alone may set statistics_enabled; build-catalog
# principals alone reach a <domain>_build catalog; $refs readers alone read `t$refs`.
# provisional until S01
PIPELINE_PRINCIPALS = (r"dbt_.*", r"pub_.*", r"svc\.runs", r"svc\.platform", r"svc\.catalog")
BUILD_CATALOG_PRINCIPALS = (r"dbt_.*", r"pub_.*", r"svc\.runs", r"svc\.platform")
REFS_READERS = ("svc.runs", "svc.catalog", "svc.platform")

SLUG = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")
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
    if not SLUG.fullmatch(name):
        return "must be lower-case words joined by single hyphens, so its fence holds no __"
    if fence(name) in RESERVED_PRODUCT_NAMES:
        return f"{fence(name)} is reserved: a lake namespace of the old layout or of the platform"
    return None


def domain_name_problem(name: str) -> str | None:
    """Why `name` cannot name a domain, or None."""
    if not SLUG.fullmatch(name):
        return "must be lower-case words joined by single hyphens, so its fence holds no __"
    if fence(name).endswith("_build"):
        return f"{fence(name)} would be the writer catalog of domain {name[: -len('-build')]}"
    if fence(name) in RESERVED_DOMAIN_NAMES:
        return f"{fence(name)} is {RESERVED_DOMAIN_NAMES[fence(name)]}"
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
    """`<fence>`: the product's reader views, or a platform product's tables. provisional until S01"""
    return fence(product)


def base_namespace(product: str) -> str:
    """`<fence>__base`: the tables dbt builds; never named by a reader rule. provisional until S01"""
    return fence(product) + BASE_SUFFIX


def builder(product: str) -> str:
    """`dbt_<fence>`: owns the base namespace in `_build`, holds no GRANT_SELECT (A8)."""
    return f"dbt_{fence(product)}"


def publisher(product: str) -> str:
    """`pub_<fence>`: owns the reader namespace in `_build`, moves the tag. provisional until S01"""
    return f"pub_{fence(product)}"


def group(product: str, role: str) -> str:
    """`<fence>__<role>`: the file group provider's group for one role on one product."""
    return f"{fence(product)}__{role}"


def dag_id(product: str) -> str:
    """The product's Airflow DAG id, also its dbt selector."""
    return fence(product)


def view_relation(domain: str, product: str, table: str) -> str:
    """What readers query: `<domain>.<fence>.<table>`, a view for a dbt or code output. provisional until S01"""
    return f"{reader_catalog(domain)}.{reader_namespace(product)}.{table}"


def view_build_relation(domain: str, product: str, table: str) -> str:
    """Where the publisher creates that view: `<domain>_build.<fence>.<table>`. provisional until S01"""
    return f"{build_catalog(domain)}.{reader_namespace(product)}.{table}"


def base_relation(domain: str, product: str, table: str) -> str:
    """A built table through the reader catalog, `<domain>.<fence>__base.<table>`: what the view
    and other products' inputs read at the tag. provisional until S01"""
    return f"{reader_catalog(domain)}.{base_namespace(product)}.{table}"


def build_relation(domain: str, product: str, table: str) -> str:
    """Where dbt writes: `<domain>_build.<fence>__base.<table>`. provisional until S01"""
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
    """`k2_release_<sha12>`: the user only a release's rules admit, which release-verify logs
    in as. Its sha12 is the document set's, since the release id hashes rules.json itself."""
    return f"k2_release_{set_sha[:12]}"


# --- Schemas ---------------------------------------------------------------------------


def document_kind(path: str) -> str | None:
    """The kind (a key of SCHEMAS) of a document-set path; None for a file no schema
    covers: models, unit tests, seeds, fixtures, parsers, the schemas themselves."""
    if path in (DOMAINS, ROLES, PRINCIPALS, EXPECTATIONS):
        return {DOMAINS: "domains", ROLES: "roles", PRINCIPALS: "principals", EXPECTATIONS: "expectations"}[path]
    parts = path.split("/")
    if len(parts) != 4 or parts[0] != "contracts" or parts[1] in RESERVED_DOMAIN_NAMES:
        return None
    if parts[3] == "product.odps.yaml":
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
BuildKind = Literal["connector", "dbt", "platform", "code"]
Backup = Literal["restore", "rebuild"]
TimeMeaning = Literal["event", "publication", "valid", "known"]
AsOf = Literal["valid", "known"]
ConnectorKind = Literal["kafka", "public-api", "https-file", "s3-file", "sftp", "ftp"]
PrincipalKind = Literal["person", "service"]
PolicyKind = Literal["delay", "mask", "rowScope"]
Severity = Literal["error", "warning"]


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
    physical_type: str  # Trino's spelling
    logical_type: str | None
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
    physical_name: str  # <fence>.<table>
    purpose: str
    owner: str
    layer: Layer
    build: Build
    backup: Backup
    retention: int | None  # days; None takes the 7-day floor
    partitioning: tuple[str, ...]
    sort: tuple[str, ...]
    columns: tuple[Column, ...]
    roles: tuple[TableRole, ...]
    policies: Mapping[str, RolePolicy]  # role -> policy; a role absent here sees everything
    quality: tuple[Quality, ...]
    sla: tuple[Sla, ...]


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
    seeds: Mapping[str, bytes]  # path under seeds/ -> bytes


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
    """manifest.json (A1, contracts/schema/manifest.schema.json)."""

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
    secrets: Mapping[str, str]


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
    VERSION AS OF 'audited'` (ADR-061 condition 2). provisional until S01"""

    relation: str  # view_relation(): the reader name
    base: str  # base_relation(): read through the reader catalog
    columns: tuple[str, ...]  # the contract's columns in order: C of ADR-061 condition 4
    owner: str  # publisher()


def load(docs: DocumentSet, images: Mapping[str, str] | None = None) -> Model:
    """Parse a document set into the model, refusing any document `schema_errors` rejects."""
    raise NotImplementedError("S04")
