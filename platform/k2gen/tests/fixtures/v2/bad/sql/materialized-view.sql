-- expect: sql.materialized-view
{{ config(materialized='materialized_view') }}
select station_id, count(*) as readings
from {{ k2_input('station_readings', 'silver_readings') }}
group by 1
