"""Emits model.API_PRINCIPALS, compiled/api/principals.json: {"principals": [<id>, ...]}, sorted, the
principals k2-api may act as (from contracts/access/principals.yaml; never dbt_*, pub_* or
svc.runs, svc.platform, svc.catalog, svc.api). k2-api's key map, {sha256 of a key: principal},
is ${K2_SECRETS}/api/keys.json (model.SECRET_FILES), never in a release, and k2-api serves a key
only when its principal is listed here. No hash, key or password is read or written here.
Filled by step S14.
"""

import json
import re

from k2gen.model import API_PRINCIPALS, Model

# The pipeline's identities and k2-api's own: svc.api impersonates a caller, never one of these (A10).
NEVER_A_CALLER = re.compile(r"(dbt|pub)_.*|svc\.(runs|platform|catalog|api)")


def emit(model: Model) -> dict[str, str]:
    callers = sorted(p.id for p in model.principals if not NEVER_A_CALLER.fullmatch(p.id))
    return {API_PRINCIPALS: json.dumps({"principals": callers}, indent=2) + "\n"}
