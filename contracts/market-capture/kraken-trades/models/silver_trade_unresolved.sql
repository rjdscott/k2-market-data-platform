-- What this does and why: the held table. Every trade of each bronze trade frame that
-- silver_trade does not hold after this build's merge: silver_trade reads every frame not yet in
-- it and takes each whose trades all resolve, so what is left is a frame with a trade whose symbol
-- resolves to nothing at the trade's time. Rebuilt whole on every build (the dbt-patterns held
-- table), so a frame leaves it on the build that moves it into silver_trade. Its warn test
-- unresolved_rows_zero reports any row and the build still publishes. Bronze is read at main, as
-- silver_trade read it: the product's DAG publishes bronze before dbt and runs one at a time.
-- ponytail: the same anti-join as silver_trade's, every key of both; the parse is of held frames only.
{{ config(materialized='table') }}
with frames as (
  select
    b.kafka_partition,
    b.kafka_offset,
    b.recv_ts_ns,
    b.conn_id,
    b.conn_msg_seq,
    json_parse(from_utf8(b.payload)) as frame
  from {{ source('kraken_trades', 'bronze_trade') }} as b
  where not exists (
    select 1 from {{ ref('kraken_trades__silver_trade') }} as s
    where s.kafka_partition = b.kafka_partition and s.kafka_offset = b.kafka_offset
  )
)

select
  f.kafka_partition,
  f.kafka_offset,
  cast(t.ordinal as integer) as trade_ordinal,
  cast(at_timezone(from_iso8601_timestamp_nanos(json_extract_scalar(t.trade, '$.timestamp')), 'UTC') as timestamp(6) with time zone) as trade_ts,
  cast(json_extract(t.trade, '$.trade_id') as bigint) as trade_id,
  json_extract_scalar(t.trade, '$.symbol') as venue_symbol,
  json_extract_scalar(t.trade, '$.side') as side,
  cast(json_extract(t.trade, '$.price') as decimal(38, 18)) as price,
  cast(json_extract(t.trade, '$.qty') as decimal(38, 18)) as quantity,
  json_extract_scalar(t.trade, '$.ord_type') as order_type,
  json_extract_scalar(f.frame, '$.type') as frame_type,
  cast(from_unixtime_nanos(f.recv_ts_ns - f.recv_ts_ns % 1000) as timestamp(6) with time zone) as received_at,
  f.conn_id,
  f.conn_msg_seq
from frames as f
cross join unnest(cast(json_extract(f.frame, '$.data') as array(json))) with ordinality as t (trade, ordinal)
where json_extract_scalar(f.frame, '$.channel') = 'trade'
