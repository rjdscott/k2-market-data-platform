-- Every domain's dq.dbt_test_results (macros/record_test_results.sql), with the domain it
-- belongs to: one union branch per catalog that holds a model of this project, so a domain
-- with marts is in as soon as its table exists (dbt_marts creates it before the domain's other
-- tasks, the hook on a hand run), and one with no table yet is left out rather than breaking
-- the view. The list is fixed when the view is built, which
-- dbt_marts does after every other domain in each run (docker/airflow/dags/dbt_marts.py).
-- The first branch is empty and types the columns, so the view builds with no domain at all.
-- A row counts for a domain only if that domain's writer wrote it: on the stack nobody else
-- may insert there (rules.json), and make test-dbt's local catalogs, which share one
-- metastore and so one dq.dbt_test_results, are kept apart by it.
-- Not a source(): these are platform plumbing, not lake tables, and carry no contract (ADR-046).
{%- set found = [] -%}
{%- if execute -%}
    {%- set catalogs = graph.nodes.values() | selectattr('resource_type', 'equalto', 'model')
        | map(attribute='database') | unique | sort -%}
    {%- for catalog in catalogs -%}
        {%- set relation = adapter.get_relation(database=catalog, schema='dq', identifier='dbt_test_results') -%}
        {%- if relation is not none -%}
            {%- do found.append((catalog, relation)) -%}
        {%- endif -%}
    {%- endfor -%}
{%- endif %}

select
    cast(null as varchar) as domain,
    cast(null as varchar) as invocation_id,
    cast(null as timestamp(6) with time zone) as run_started_at,
    cast(null as varchar) as unique_id,
    cast(null as varchar) as test_name,
    cast(null as varchar) as tested,
    cast(null as varchar) as status,
    cast(null as bigint) as failures,
    cast(null as double) as execution_time,
    cast(null as varchar) as message
where false
{%- for catalog, relation in found %}
union all
select
    '{{ catalog | replace("_", "-") }}' as domain,
    invocation_id,
    run_started_at,
    unique_id,
    test_name,
    tested,
    status,
    failures,
    execution_time,
    message
from {{ relation }}
where writer = 'dbt_{{ catalog }}'
{%- endfor %}
