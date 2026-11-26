SELECT "station_id", "read_at", "value", "region", "operator_note" FROM "sensor_net"."station_readings__base"."silver_readings" FOR VERSION AS OF 'audited'
