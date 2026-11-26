{#- A7 as written: the version clause whenever k2_inputs holds the key, unit tests included. -#}
{% macro k2_input(product, table) -%}
  {%- set inputs = var('k2_inputs', none) -%}
  {%- set key = product ~ '/' ~ table -%}
  {%- if inputs is none -%}
    {{ source(product, table) }}
  {%- elif key not in inputs -%}
    {{ exceptions.raise_compiler_error("k2_input: k2_inputs is set but has no entry " ~ key) }}
  {%- else -%}
    {{ source(product, table) }} FOR VERSION AS OF {{ inputs[key]['snapshot'] }}
  {%- endif -%}
{%- endmacro %}
