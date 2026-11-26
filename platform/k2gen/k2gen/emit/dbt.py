"""Emits compiled/dbt/, one dbt project named model.DBT_PROJECT: each table's model as
models/<domain fence>/<fence>/<model.dbt_model>.sql with `alias: <table>`, written by the naming
macro to model.build_relation; dbt_project.yml with +view_security: invoker and on_table_exists:
replace; sources: input ports at model.input_relation through the reader catalog, own bronze on
lake; model YAML from ODCS (contract enforced, unique_key from primaryKey, tests scoped by where);
unit_tests.yml as written (its `model:` is the dbt name); one selector per product (model.dag_id);
declared seeds; dbt/macros/k2_input.sql and k2_results.sql copied. Skips build serving.
Filled by step S17.
"""

from k2gen.model import Model


def emit(model: Model) -> dict[str, str]:
    raise NotImplementedError("S17")
