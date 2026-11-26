"""Emits compiled/trino/etc/: config.properties (HTTPS only on 8443, keystore
/run/k2-secrets/tls/trino.pem, password authentication, the shared secret as
${ENV:K2_TRINO_SHARED_SECRET}), jvm.config, node.properties; catalog/: lake, a reader per domain
(iceberg.security=read_only, iceberg.table-statistics-enabled=false) and a _build writer per
domain, keys only as ${ENV:...}; access-control.properties (rules.json through the pointer, 5 s
refresh), group-provider.properties (groups.txt the same way) and the resource groups of A8.
Filled by step S11.
"""

from k2gen.model import Model


def emit(model: Model) -> dict[str, str]:
    raise NotImplementedError("S11")
