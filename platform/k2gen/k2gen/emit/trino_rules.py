"""Emits compiled/trino/rules.json and compiled/trino/groups.txt (A8, ADR-061): builders own
<fence>__base without GRANT_SELECT; publishers own <fence> in _build, select and GRANT_SELECT
their own base through the reader catalog, and have catalog access all on reader catalogs
(model.READER_CATALOG_ALL); svc.platform holds nothing in a reader namespace; reader rules on
exact view names (masks to typed nulls, delay and rowScope filters, values via model.sql_literal),
never a base; no authorization or procedure rules; model.STATISTICS_PROPERTY only for
model.PIPELINE_PRINCIPALS, model.RETENTION_PROPERTIES only for
model.RETENTION_PROPERTY_PRINCIPALS; $refs for model.REFS_READERS; the svc.api impersonation and
model.canary_user. Skips build serving.
Filled by step S12.
"""

from k2gen.model import Model


def emit(model: Model) -> dict[str, str]:
    raise NotImplementedError("S12")
