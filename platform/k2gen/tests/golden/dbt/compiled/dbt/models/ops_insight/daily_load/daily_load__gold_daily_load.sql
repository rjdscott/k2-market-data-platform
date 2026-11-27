-- Synthetic: readings per station per day, read from the input's tested snapshot.
{{ config(materialized='table') }}
select
  cast(read_at as date) as load_date,
  station_id,
  count(*) as readings,
  cast(avg(value) as decimal(12, 3)) as mean_value
from {{ k2_input('station_readings', 'silver_readings') }}
group by 1, 2
