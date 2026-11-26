"""Emits compiled/lake/ddl/ for Spark and platform tables; compiled/trino/views/<relation>.sql per dbt
output, exactly SELECT <columns> FROM <base> FOR VERSION AS OF 'audited' (ADR-061 condition 2);
model.VIEWS; contract_schemas.json, retention.json, storage-plan.json (warehouses at
remote-signing-enabled false, both namespaces) and backup-set.json.

Filled by step S15.
"""

from k2gen.model import Model, Release


def emit(model: Model, previous: Release | None) -> dict[str, str]:
    raise NotImplementedError("S15")
