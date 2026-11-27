{#- The generic tests k2gen adds to dbt's own (not_null, unique, relationships).

    k2_unique_key(model, columns): fails on every combination of the columns more than one row
    holds: a contract's primaryKey of two or more columns (one column is dbt's unique).

    k2_input_relationships(model, column_name, product_fence, table, field): dbt's relationships
    test against another product's table read through k2_input, so the test reads the snapshot
    the build read, never that table's untested main.

    k2_quality(model, query, holds): an ODCS sql or library quality rule. The query, with
    ${table} as the model, returns one column, the measured value; the test returns each value
    for which `holds`, the rule's thresholds as a condition on metric_value, is not true, so a
    null value fails too. k2gen writes both arguments (emit/dbt.py quality_test). -#}
{% test k2_unique_key(model, columns) -%}
  {%- set key = [] -%}
  {%- for c in columns -%}{%- do key.append(adapter.quote(c)) -%}{%- endfor -%}
select {{ key | join(', ') }}, count(*) as row_count
from {{ model }}
group by {{ key | join(', ') }}
having count(*) > 1
{%- endtest %}

{% test k2_input_relationships(model, column_name, product_fence, table, field) -%}
  {{ default__test_relationships(model, column_name, k2_input(product_fence, table), field) }}
{%- endtest %}

{% test k2_quality(model, query, holds) -%}
select metric_value
from ({{ query | replace('${table}', model) }}) as k2_quality (metric_value)
where not coalesce({{ holds }}, false)
{%- endtest %}
