{{ config(materialized='incremental', incremental_strategy='merge', unique_key='id', on_schema_change='append_new_columns') }}
{% set p = var('phase', 1) %}
{% if p == 1 %}
select cast(id as integer) id, cast(name as varchar) name, cast(amount as integer) amount
from (values (1, 'a', 10), (2, 'b', 20), (3, 'c', 30)) t(id, name, amount)
{% elif p == 2 %}
select cast(id as integer) id, cast(name as varchar) name, cast(amount as integer) amount
from (values (2, 'b2', 21), (3, 'c2', 31), (4, 'd', 40)) t(id, name, amount)
{% elif p == 3 %}
select cast(id as integer) id, cast(name as varchar) name, cast(amount as integer) amount, cast(extra as varchar) extra
from (values (4, 'd3', 41, 'x'), (5, 'e', 50, 'y')) t(id, name, amount, extra)
{% else %}
{# phase 4, added after the spike: new keys only, so the merge matches no row #}
select cast(id as integer) id, cast(name as varchar) name, cast(amount as integer) amount, cast(extra as varchar) extra
from (values (6, 'f', 60, 'z')) t(id, name, amount, extra)
{% endif %}
