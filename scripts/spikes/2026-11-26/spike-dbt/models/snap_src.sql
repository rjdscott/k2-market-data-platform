{% if var('snap_phase', 1) == 1 %}
select cast(id as integer) id, cast(colour as varchar) colour, cast(updated_at as timestamp(6)) updated_at
from (values (1, 'red', timestamp '2026-01-01 00:00:00'), (2, 'blue', timestamp '2026-01-01 00:00:00')) t(id, colour, updated_at)
{% else %}
select cast(id as integer) id, cast(colour as varchar) colour, cast(updated_at as timestamp(6)) updated_at
from (values (1, 'red', timestamp '2026-01-01 00:00:00'), (2, 'green', timestamp '2026-01-02 00:00:00'), (3, 'yellow', timestamp '2026-01-02 00:00:00')) t(id, colour, updated_at)
{% endif %}
