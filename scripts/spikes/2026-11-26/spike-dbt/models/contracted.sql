{% if var('bad_type', false) %}
select cast('x1' as varchar) as id, cast(2.5 as double) as amount
{% else %}
select cast(1 as integer) as id, cast(1.5 as double) as amount
{% endif %}
