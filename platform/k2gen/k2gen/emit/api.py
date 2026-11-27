"""Emits model.API_PRINCIPALS, compiled/api/principals.json: {"principals": [<id>, ...]}, sorted, the
principals k2-api may act as: exactly those the access rules let svc.api impersonate
(emit/trino_rules.impersonable, the declared `p.*` people), so k2-api never serves a key for a
principal Trino would refuse it to become, and never a pipeline identity or its own. k2-api's key
map, {sha256 of a key: principal}, is ${K2_SECRETS}/api/keys.json (model.SECRET_FILES), never in a
release, and k2-api serves a key only when its principal is listed here. No hash, key or password
is read or written here. Filled by step S14.
"""

import json

from k2gen.emit.trino_rules import impersonable
from k2gen.model import API_PRINCIPALS, Model


def emit(model: Model) -> dict[str, str]:
    return {API_PRINCIPALS: json.dumps({"principals": impersonable(model)}, indent=2) + "\n"}
