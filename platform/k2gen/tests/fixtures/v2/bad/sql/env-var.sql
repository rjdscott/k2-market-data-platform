-- expect: sql.env-var
-- The model renders a variable of the builder's environment into its SQL.
{{ config(materialized='table') }}
select
  station_id,
  '{{ env_var("K2_TRINO_USER") }}' as built_by
from {{ k2_input('station_readings', 'silver_readings') }}
