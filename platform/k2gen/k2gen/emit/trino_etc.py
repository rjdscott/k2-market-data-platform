"""Emits compiled/trino/etc/: config.properties (HTTPS only, password authentication, the shared secret as
${ENV:K2_TRINO_SHARED_SECRET}), jvm.config, node.properties; catalogs lake, a reader per domain
(read_only, table statistics off) and a _build writer per domain, keys only as ${ENV:...};
access-control.properties (5 s refresh through the pointer), group-provider.properties and the
resource groups of A8.

Filled by step S11.
"""

from k2gen.model import Model, Release


def emit(model: Model, previous: Release | None) -> dict[str, str]:
    raise NotImplementedError("S11")
