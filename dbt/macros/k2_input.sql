{#- k2_input(product_fence, table): an upstream product's table at the snapshot the wrapper resolved
    (phase 001 interface A7, docs/development/phase-001-interfaces.md). Spike d's macro
    (scripts/spikes/2026-11-26/spine-d-k2-input/, docs/research/2026-11-26-release-spine-spikes.md),
    its parameter named as A7 freezes it. The shape that holds in one dbt build:
      k2_inputs absent                           -> source(), unpinned (a developer's run)
      k2_inputs set, no "<product_fence>/<table>" -> a compiler error, at parse
      a unit test                                -> source(), which dbt has already replaced with the
                                                    fixture's CTE (__dbt__cte__<source>__<table>), and a
                                                    CTE takes no FOR VERSION AS OF
      otherwise                                  -> source() FOR VERSION AS OF <snapshot>
    The var is {"k2_inputs": {"<product_fence>/<table>": {"snapshot": <id>}}}, set by the wrapper only.
    The snapshot goes through `int`, so the var can only ever add a number to the SQL, and is
    typed BIGINT: an id under 2^31 is an integer literal, which Trino refuses as a table version.
    dbt/tests/k2_input/ proves each case with no warehouse (make test-dbt). -#}
{% macro k2_input(product_fence, table) -%}
  {%- set rel = source(product_fence, table) -%}
  {%- set inputs = var('k2_inputs', none) -%}
  {%- set key = product_fence ~ '/' ~ table -%}
  {%- if inputs is none -%}
    {{ rel }}
  {%- elif key not in inputs -%}
    {{ exceptions.raise_compiler_error("k2_input: k2_inputs is set but has no entry " ~ key) }}
  {%- elif model.resource_type == 'unit_test' -%}
    {{ rel }}
  {%- else -%}
    {{ rel }} FOR VERSION AS OF BIGINT '{{ inputs[key]['snapshot'] | int }}'
  {%- endif -%}
{%- endmacro %}
