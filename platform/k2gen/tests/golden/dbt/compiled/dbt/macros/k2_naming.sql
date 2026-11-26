{#- Where a model or seed is built: the +database and +schema its product folder sets in
    dbt_project.yml, <domain>_build and <fence>__base, the schema as written (dbt's own rule would
    prefix the profile's schema). A model or seed outside a product folder is a compiler error,
    never a write elsewhere. A test keeps dbt's default: it writes nothing. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- if custom_schema_name is not none -%}
    {{ custom_schema_name | trim }}
  {%- elif node.resource_type in ('model', 'seed', 'snapshot') -%}
    {{ exceptions.raise_compiler_error(node.resource_type ~ " " ~ node.name ~ " is in no product folder of dbt_project.yml") }}
  {%- else -%}
    {{ default__generate_schema_name(custom_schema_name, node) }}
  {%- endif -%}
{%- endmacro %}
