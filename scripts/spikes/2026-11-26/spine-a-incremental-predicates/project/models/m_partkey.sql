-- The partition column in the unique key instead of a predicate.
{{ config(unique_key=['event_date', 'id']) }}
select id, event_date, amount, loaded_at
from {{ source('src', 'events') }}
{% if is_incremental() %}
-- The batch by a var, not max(loaded_at) from {{ this }}, so the target table's one scan is the MERGE's.
where loaded_at > TIMESTAMP '{{ var("since") }}'
{% endif %}
