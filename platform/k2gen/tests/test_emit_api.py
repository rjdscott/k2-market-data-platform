"""emit/api.py (S14): whom k2-api may act as, compiled from the principals, with no credential.

Its golden is the good fixture set's (tests/golden/api/); the repository's own principals are
held to the same rule, so the canary's readers are callers and no platform identity is.
"""

import json
from dataclasses import replace
from pathlib import Path

from test_model import docset, good
from test_repository_documents import repository_set

from k2gen import model
from k2gen.emit import api

GOLDEN = Path(__file__).parent / "golden" / "api"


def test_the_good_set_compiles_to_its_golden_caller_list() -> None:
    out = api.emit(model.load(docset(good())))
    assert out == {model.API_PRINCIPALS: (GOLDEN / model.API_PRINCIPALS).read_text()}


def test_no_pipeline_or_api_identity_is_ever_a_caller() -> None:
    m = model.load(docset(good()))
    planted = [model.Principal(i, "service", "platform-team", None)
               for i in ("dbt_x", "pub_x", "svc.runs", "svc.platform", "svc.catalog", "svc.api")]
    callers = json.loads(api.emit(replace(m, principals=m.principals + tuple(planted)))[model.API_PRINCIPALS])["principals"]
    assert callers == ["p.analyst-delayed", "p.analyst-full", "svc.load-dashboard"]
    # A look-alike is a consumer service, not one of the six: the match is whole-name.
    lookalike = model.Principal("svc.api-dashboard", "service", "insight-team", None)
    assert "svc.api-dashboard" in json.loads(api.emit(replace(m, principals=(lookalike,)))[model.API_PRINCIPALS])["principals"]


def test_the_repository_callers_are_the_canary_readers() -> None:
    out = json.loads(api.emit(model.load(repository_set()))[model.API_PRINCIPALS])
    assert out == {"principals": ["p.canary-delayed", "p.canary-full", "p.canary-scoped"]}
