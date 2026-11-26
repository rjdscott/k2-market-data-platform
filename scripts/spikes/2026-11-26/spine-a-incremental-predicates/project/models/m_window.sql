-- unique key id, and a 3-day window on the target: rows of 2026-01-08 and later.
{{ config(unique_key='id', incremental_predicates=["DBT_INTERNAL_DEST.event_date >= DATE '2026-01-08'"]) }}
select id, event_date, amount, loaded_at
from {{ source('src', 'events') }}
{% if is_incremental() %}
-- The batch by a var, not max(loaded_at) from {{ this }}, so the target table's one scan is the MERGE's.
where loaded_at > TIMESTAMP '{{ var("since") }}'
{% endif %}
