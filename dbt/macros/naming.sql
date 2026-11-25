{#
  Where a model lives comes from its folder, so the layout is the naming rule
  (ADR-044: one catalog per domain, one schema per data product):

    models/<catalog>/staging/<model>.sql          -> <catalog>.staging
    models/<catalog>/intermediate/<model>.sql     -> <catalog>.intermediate
    models/<catalog>/marts/<product>/<model>.sql  -> <catalog>.<product>

  node.fqn is [project, <catalog>, <layer>, ..., <model>]. Anything that is not a
  model (a test, a unit test) keeps dbt's default, the profile's catalog.
#}

{% macro generate_database_name(custom_database_name, node) -%}
    {%- if node.resource_type == 'model' -%}
        {{ node.fqn[1] }}
    {%- else -%}
        {{ default__generate_database_name(custom_database_name, node) }}
    {%- endif -%}
{%- endmacro %}

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if node.resource_type != 'model' -%}
        {{ default__generate_schema_name(custom_schema_name, node) }}
    {%- elif node.fqn | length == 4 and node.fqn[2] in ('staging', 'intermediate') -%}
        {{ node.fqn[2] }}
    {%- elif node.fqn | length == 5 and node.fqn[2] == 'marts' -%}
        {{ node.fqn[3] }}
    {%- else -%}
        {{ exceptions.raise_compiler_error(
            "model " ~ node.name ~ " is at models/" ~ node.fqn[1:-1] | join('/')
            ~ ": put it in models/<catalog>/staging/, models/<catalog>/intermediate/ or models/<catalog>/marts/<product>/") }}
    {%- endif -%}
{%- endmacro %}
