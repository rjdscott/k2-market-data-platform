-- expect: sql.config-unreadable
-- dbt reads a config() in a do tag; model.model_config reads only the {{ config(...) }} form.
{% do config(materialized='view') %}
select station_id from {{ k2_input('station_readings', 'silver_readings') }}
