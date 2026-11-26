{% if var('bad', false) %}
select cast(id as integer) id, cast(v as varchar) v from (values (1, 'bad1'), (1, 'bad1dup'), (2, 'bad2')) t(id, v)
{% else %}
select cast(id as integer) id, cast(v as varchar) v from (values (1, 'good1'), (2, 'good2')) t(id, v)
{% endif %}
