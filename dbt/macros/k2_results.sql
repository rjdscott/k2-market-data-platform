{#- k2_results(results): the per-product results hook (phase 001 interface A7,
    docs/development/phase-001-interfaces.md). A STUB: phase 002 builds it with the dbt wrapper,
    and nothing calls it before then; called at run time, it fails rather than drop results.
    Frozen: the signature, an on-run-end hook taking dbt's `results`; one row per data test and
    unit test of the invocation, failed ones included, in the columns record_test_results.sql
    writes today plus run_id (<dag_id>/<run_id>, the var k2_run_id the wrapper sets) and
    dbt_invocation_id (dbt's invocation_id), the two keys that join a publish's run record to its
    test rows; into <domain>_build.<fence>__base.dq_test_results, as the builder dbt_<fence>, which
    owns only <fence>__base (ADR-061). Unioned for readers by a generated governance view. -#}
{% macro k2_results(results) -%}
  {%- if execute -%}
    {{ exceptions.raise_compiler_error("k2_results: a stub until phase 002 builds the dbt wrapper") }}
  {%- endif -%}
{%- endmacro %}
