"""Emits, for every table in model.LAKE_BUILDS:
- model.ddl_path DDL: spark for warehouse k2 (connector tables, and model.PLATFORM_TABLES per
  connector product), trino for platform tables (model.RUN_RECORD_SQL, copied);
- model.view_sql_path per dbt or code output, exactly SELECT <columns> FROM <base> FOR VERSION AS
  OF 'audited', every name through model.quote_ident (ADR-061 part 1), and model.VIEWS;
- model.CONTRACT_SCHEMAS, model.RETENTION (the one retention source: the contracts' retention, the
  7-day floor when absent), model.STORAGE_PLAN and model.BACKUP_SET, each against its schema.
Filled by step S15.
"""

from k2gen.model import Model


def emit(model: Model) -> dict[str, str]:
    raise NotImplementedError("S15")
