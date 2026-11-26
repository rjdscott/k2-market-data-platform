-- expect: sql.time-travel
-- A snapshot picked by hand.
{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['load_date', 'station_id']) }}
select cast(read_at as date) as load_date, station_id, count(*) as readings
from {{ k2_input('station_readings', 'silver_readings') }}
{% if is_incremental() %}
where cast(read_at as date) > (select max(load_date) from {{ this }} for version as of 1)
{% endif %}
group by 1, 2
