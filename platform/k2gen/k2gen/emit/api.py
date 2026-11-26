"""Emits model.API_PRINCIPALS, compiled/api/principals.json: {"principals": [<id>, ...]}, sorted, the
principals k2-api may act as (from contracts/access/principals.yaml; never dbt_*, pub_* or
svc.runs, svc.platform, svc.catalog, svc.api). k2-api's key map, {sha256 of a key: principal},
is ${K2_SECRETS}/api/keys.json (model.SECRET_FILES), never in a release.
Filled by step S14.
"""

from k2gen.model import Model


def emit(model: Model) -> dict[str, str]:
    raise NotImplementedError("S14")
