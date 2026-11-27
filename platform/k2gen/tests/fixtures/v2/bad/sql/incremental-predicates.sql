-- expect: sql.incremental-predicates
-- A merge narrowed to the last week: older rows are no longer matched on the key.
{{ config(materialized='incremental', incremental_strategy='merge', unique_key=['load_date', 'station_id'],
          incremental_predicates=["DBT_INTERNAL_DEST.load_date > current_date - interval '7' day"]) }}
select cast(read_at as date) as load_date, station_id, count(*) as readings
from {{ k2_input('station_readings', 'silver_readings') }}
group by 1, 2
