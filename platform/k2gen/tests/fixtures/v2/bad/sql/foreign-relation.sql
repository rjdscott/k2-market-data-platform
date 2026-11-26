-- expect: sql.foreign-read
-- Another product's base named by hand.
{{ config(materialized='table') }}
select station_id from sensor_net.station_readings__base.silver_readings
