-- Synthetic: each landed reading typed, merged on the key past the newest reading time.
{{ config(materialized='incremental', incremental_strategy='merge') }}
select
  station_id,
  read_at,
  cast(value_text as decimal(12, 3)) as value,
  region,
  operator_note
from {{ source('station_readings', 'bronze_readings') }}
{% if is_incremental() %}
where read_at > (select max(read_at) from {{ this }})
{% endif %}
