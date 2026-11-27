"""emit/api.py (S14): whom k2-api may act as, compiled from the principals, with no credential.

Its golden is the good fixture set's (tests/golden/api/); the repository's own principals are
held to the same rule, so the canary's readers are callers and no platform identity is. The
callers are compared with the rules emitter's impersonation rule for the same model.
"""

import json
import re
from dataclasses import replace
from pathlib import Path

from test_model import docset, good
from test_repository_documents import repository_set

from k2gen import model
from k2gen.emit import api, trino_rules

GOLDEN = Path(__file__).parent / "golden" / "api"


def test_the_good_set_compiles_to_its_golden_caller_list() -> None:
    out = api.emit(model.load(docset(good())))
    assert out == {model.API_PRINCIPALS: (GOLDEN / model.API_PRINCIPALS).read_text()}


def test_the_callers_are_exactly_whom_the_rules_let_svc_api_become() -> None:
    """Both emitters, one model: k2-api's callers equal the rules' impersonation targets, so a key
    for a service consumer (svc.load-dashboard here) is never served only for Trino to refuse it."""
    m = model.load(docset(good()))
    planted = [model.Principal(i, "service", "platform-team", None)
               for i in ("dbt_x", "pub_x", "svc.runs", "svc.platform", "svc.catalog", "svc.api", "svc.api-dashboard", "px.lookalike")]
    for mm in (m, replace(m, principals=m.principals + tuple(planted))):
        callers = json.loads(api.emit(mm)[model.API_PRINCIPALS])["principals"]
        (rule,) = json.loads(trino_rules.emit(mm)[trino_rules.RULES])["impersonation"]
        assert rule["original_user"] == "svc\\.api"
        assert callers == [u for u in trino_rules.known_principals(mm) if re.fullmatch(rule["new_user"], u)]
        assert callers == ["p.analyst-delayed", "p.analyst-full"]


def test_the_repository_callers_are_the_canary_readers() -> None:
    out = json.loads(api.emit(model.load(repository_set()))[model.API_PRINCIPALS])
    assert out == {"principals": ["p.canary-delayed", "p.canary-full", "p.canary-scoped"]}
