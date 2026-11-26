{#- k2_input(product, table): an upstream product's table at the snapshot the wrapper resolved
    (IMPLEMENTATION-N0-N1 A7). The shape that holds in one dbt build:
      k2_inputs absent                     -> source(), unpinned (a developer's run)
      k2_inputs set, no "<product>/<table>" -> a compiler error, at parse
      a unit test                          -> source(), which dbt has already replaced with the
                                              fixture's CTE (__dbt__cte__<source>__<table>), and a
                                              CTE takes no FOR VERSION AS OF
      otherwise                            -> source() FOR VERSION AS OF <snapshot>
    The snapshot goes through `int`, so the var can only ever add a number to the SQL, and is
    typed BIGINT: an id under 2^31 is an integer literal, which Trino refuses as a table version. -#}
{% macro k2_input(product, table) -%}
  {%- set rel = source(product, table) -%}
  {%- set inputs = var('k2_inputs', none) -%}
  {%- set key = product ~ '/' ~ table -%}
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
