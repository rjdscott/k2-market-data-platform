"""Emits compiled/trino/etc/: config.properties (HTTPS only on 8443, keystore
/run/k2-secrets/tls/trino.pem, password authentication, the shared secret as
${ENV:K2_TRINO_SHARED_SECRET}), jvm.config, node.properties, log.properties; catalog/: lake, a
reader per domain (iceberg.security=read_only, iceberg.table-statistics-enabled=false) and a _build
writer per domain, keys only as ${ENV:...}; access-control.properties (rules.json through the
pointer, 5 s refresh), group-provider.properties (groups.txt the same way) and the resource groups
of A8. Filled by step S11.

Trino starts from `--etc-dir=/k2/releases/pointers/trino/compiled/trino/etc` and reads all of this
once, at start (`model.NEEDS_BY_PREFIX`: a change needs `trinoRestart`). Only rules.json and
groups.txt (S12), read through the pointer, and the password file (S13) are read again while it
runs, every 5 s, so a rules-only deploy needs no restart. password-authenticator.properties beside
these files is S13's.

Every credential is an environment name, never a value (`model.SECRET_FILES`, A8): the shared
secret, and the storage key pair `K2_TRINO_S3_*` (read-write on the domain warehouses, read on k2).
"""

import json

from k2gen.model import (
    LAKE,
    LAKE_WAREHOUSE,
    PIPELINE_PRINCIPALS,
    RELEASES_MOUNT,
    SECRET_FILES,
    Domain,
    Model,
    build_catalog,
    reader_catalog,
    warehouse,
)

ETC = "compiled/trino/etc/"
HTTPS_PORT = 8443  # A10; K2_TRINO_INTERNAL_PORT on the network, published on the host as K2_TRINO_PORT
POINTER = f"{RELEASES_MOUNT}/pointers/trino"  # A1: swapped by `mv -T`, read here by path, never resolved once
REFRESH = "5s"  # rules and groups, each on its own timer: release-verify waits two periods
CATALOG_URI = "http://lakekeeper:8181/catalog"  # Lakekeeper's name on k2-catalog (A8), as on k2-net today
S3_ENDPOINT = "http://minio:9000"
SHARED_SECRET = "K2_TRINO_SHARED_SECRET"
S3_ACCESS_KEY, S3_SECRET_KEY = "K2_TRINO_S3_ACCESS_KEY", "K2_TRINO_S3_SECRET_KEY"
KEYSTORE = next(f.mount for f in SECRET_FILES if f.path == "tls/trino.pem")
PIPELINE, INTERACTIVE = "pipeline", "interactive"
HEADER = "# Compiled by k2gen (emit/trino_etc.py) from the release's documents. Never edited.\n"

CONFIG = f"""{HEADER}\
# One node: the coordinator is also the only worker.
# Not carried over from the image's default: its 2GB fs.memory-cache
# (cache-manager-memory.properties), which would not fit beside the heap.
coordinator=true
node-scheduler.include-coordinator=true
# HTTPS only (A10): no plain HTTP listener. The server's key and certificate are one PEM file
# under ${{K2_SECRETS}} (model.SECRET_FILES), and every client logs in with a password.
http-server.http.enabled=false
http-server.https.enabled=true
http-server.https.port={HTTPS_PORT}
http-server.https.keystore.path={KEYSTORE}
http-server.authentication.type=PASSWORD
# With plain HTTP off the node reaches itself over HTTPS too, on a certificate Trino makes from
# the shared secret (https.required; without it the start fails, `internalUri is null`), which
# takes discovery.uri by IP address only. Trino then holds two certificates and picks one by the
# name a client asks for (SNI): clients connect by name (trino, localhost), because one that
# connects by IP address is shown the internal certificate and refuses it.
internal-communication.shared-secret=${{ENV:{SHARED_SECRET}}}
internal-communication.https.required=true
discovery.uri=https://127.0.0.1:{HTTPS_PORT}
"""

# Heap 2816M under the service's 5G limit leaves 2.25 GiB for metaspace, the code cache,
# thread stacks and direct buffers, which nothing caps (docker/trino/README.md). The file is
# the live one, without comments.
JVM = """\
-server
-agentpath:/usr/lib/trino/bin/libjvmkill.so
-Xms2816M
-Xmx2816M
-Duser.timezone=UTC
-XX:G1HeapRegionSize=32M
-XX:+ExplicitGCInvokesConcurrent
-XX:+ExitOnOutOfMemoryError
-XX:-OmitStackTraceInFastThrow
-XX:ReservedCodeCacheSize=256M
-XX:PerMethodRecompilationCutoff=10000
-XX:PerBytecodeRecompilationCutoff=10000
-Djdk.attach.allowAttachSelf=true
-Djdk.nio.maxCachedBufferSize=2000000
"""

NODE = f"""{HEADER}\
node.environment=k2
node.data-dir=/data/trino
"""

LOG = f"""{HEADER}\
# Trino logs every catalog property at start, one INFO line each, under the logger
# io.trino.bootstrap.catalog.<catalog>, and masks only the properties its connector marks
# security-sensitive: s3.aws-secret-key is one, s3.aws-access-key is not, so the access key
# would be in the log once per catalog per start. WARN on the parent logger silences those
# lines for every catalog; a property Trino refuses still fails the start with an error.
io.trino.bootstrap.catalog=WARN
"""

ACCESS_CONTROL = f"""{HEADER}\
# The rules (S12) through the pointer, reread every {REFRESH}: a deploy that changes only
# rules.json swaps the pointer and needs no restart.
access-control.name=file
security.config-file={POINTER}/compiled/trino/rules.json
security.refresh-period={REFRESH}
"""

GROUP_PROVIDER = f"""{HEADER}\
# The <fence>__<role> groups (S12) through the pointer, reread every {REFRESH}.
group-provider.name=file
file.group-file={POINTER}/compiled/trino/groups.txt
file.refresh-period={REFRESH}
"""

RESOURCE_GROUPS = f"""{HEADER}\
# Read once at start, relative to the etc directory the launcher links into its data directory.
resource-groups.configuration-manager=file
resource-groups.config-file=etc/resource-groups.json
"""

# A8: pipeline principals in `pipeline`, everyone else, svc.api's impersonated callers and the
# release canary included, in `interactive`. Trino matches a selector's user as a full regex,
# first selector wins. ponytail: one node, so both groups get the whole node and limits that
# never queue; split memory and concurrency when a pipeline query is measured waiting behind
# interactive ones (scripts/verify/resource-groups.sh shows the placement).
_GROUP_LIMITS = {"softMemoryLimit": "100%", "hardConcurrencyLimit": 100, "maxQueued": 1000}
RESOURCE_GROUPS_JSON = json.dumps({
    "rootGroups": [{"name": name, **_GROUP_LIMITS} for name in (PIPELINE, INTERACTIVE)],
    "selectors": [{"user": "|".join(PIPELINE_PRINCIPALS), "group": PIPELINE}, {"group": INTERACTIVE}],
}, indent=2) + "\n"


def _iceberg(comment: str, warehouse: str, extra: str) -> str:
    """A catalog over one Lakekeeper warehouse, with Trino's static storage keys by name."""
    return f"""{HEADER}{comment}\
connector.name=iceberg
iceberg.catalog.type=rest
iceberg.rest-catalog.uri={CATALOG_URI}
iceberg.rest-catalog.warehouse={warehouse}
# Static keys below; Lakekeeper's warehouses vend nothing.
iceberg.rest-catalog.vended-credentials-enabled=false
{extra}\
fs.s3.enabled=true
s3.endpoint={S3_ENDPOINT}
s3.region=local-01
s3.path-style-access=true
s3.aws-access-key=${{ENV:{S3_ACCESS_KEY}}}
s3.aws-secret-key=${{ENV:{S3_SECRET_KEY}}}
"""


READ_ONLY = "iceberg.security=read_only\n"
NO_STATISTICS = "iceberg.table-statistics-enabled=false\n"
# Lakekeeper assigns table locations (<warehouse>/<uuid>), and the two procedures that attach
# files from elsewhere stay off.
WRITER = """\
iceberg.rest-catalog.server-assigned-table-location-enabled=true
iceberg.register-table-procedure.enabled=false
iceberg.add-files-procedure.enabled=false
"""

LAKE_CATALOG = _iceberg(f"""\
# Catalog {LAKE}: warehouse {LAKE_WAREHOUSE}, the landings and bronze. Read-only at the connector: no
# commit of any kind, ALTER TABLE ... EXECUTE included, which file rules do not cover.
# Spark is its only writer.
""", LAKE_WAREHOUSE, READ_ONLY)


def _reader(d: Domain) -> str:
    return _iceberg(f"""\
# Domain {d.name} ({d.owner}): its reader catalog over warehouse {warehouse(d.name)}. Read-only
# at the connector, and no table statistics, so no plan and no SHOW STATS shows a bound of a
# row a reader may not see (ADR-061 part 1). pub_* holds `all` here for the definer-view owner
# check (rules.json); the connector refuses it every write all the same.
""", warehouse(d.name), READ_ONLY + NO_STATISTICS)


def _writer(d: Domain) -> str:
    return _iceberg(f"""\
# Domain {d.name} ({d.owner}): its writer catalog over warehouse {warehouse(d.name)}, which
# only builders, publishers, svc.runs and svc.platform reach (rules.json).
""", warehouse(d.name), WRITER)


def emit(model: Model) -> dict[str, str]:
    files = {
        "config.properties": CONFIG,
        "jvm.config": JVM,
        "node.properties": NODE,
        "log.properties": LOG,
        "access-control.properties": ACCESS_CONTROL,
        "group-provider.properties": GROUP_PROVIDER,
        "resource-groups.properties": RESOURCE_GROUPS,
        "resource-groups.json": RESOURCE_GROUPS_JSON,
        f"catalog/{LAKE}.properties": LAKE_CATALOG,
    }
    for d in model.domains:
        files[f"catalog/{reader_catalog(d.name)}.properties"] = _reader(d)
        files[f"catalog/{build_catalog(d.name)}.properties"] = _writer(d)
    return {ETC + path: text for path, text in files.items()}
