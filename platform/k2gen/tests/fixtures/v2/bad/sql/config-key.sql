-- expect: sql.config-key
-- dbt's grants config would run GRANT as the builder.
{{ config(materialized='table', grants={'select': ['p.analyst-full']}) }}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}
