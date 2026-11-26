"""Emits compiled/trino/rules.json and groups.txt (A8): builders own <fence>__base without GRANT_SELECT,
publishers own <fence> and select their own base; reader rules on exact view names (masks to
typed nulls, delay and rowScope filters), never a base; no authorization or procedure rules;
statistics_enabled and retention properties for model.PIPELINE_PRINCIPALS only; the svc.api
impersonation and the release canary.

Filled by step S12.
"""

from k2gen.model import Model, Release


def emit(model: Model, previous: Release | None) -> dict[str, str]:
    raise NotImplementedError("S12")
