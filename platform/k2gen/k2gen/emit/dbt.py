"""Emits compiled/dbt/: models under models/<catalog>/<product>/, dbt_project.yml with
+view_security: invoker, the naming macro writing <domain>_build.<fence>__base, sources, model
YAML from ODCS (contract enforced, unique_key from primaryKey, tests scoped by where), one
selector per product, seeds, and dbt/macros/k2_input.sql copied.

Filled by step S17.
"""

from k2gen.model import Model, Release


def emit(model: Model, previous: Release | None) -> dict[str, str]:
    raise NotImplementedError("S17")
