-- What this does and why: a Kraken WebSocket v2 trade frame carries a data array of trades. This
-- model unnests each frame WITH ORDINALITY into one typed row per trade (the ordinal is the
-- trade's place in the frame, the third part of the key), casts price and quantity from the JSON
-- numbers to exact decimals, and resolves each trade's symbol through the security master's
-- venue_symbol version valid at the trade's time (type-2, half-open [valid_from, valid_to), the
-- first version opening at 1970-01-01 so a trade before the first fetch still resolves).
-- A frame comes in only when every trade in it resolves; one that does not stays out and is held
-- in silver_trade_unresolved, so the rest of the venue publishes.
--
-- Incremental, which frames a build reads: every bronze frame no silver row comes from yet, an
-- anti-join on (kafka_partition, kafka_offset). Offsets are the watermark, never a wall clock, and
-- the anti-join is that watermark kept per frame rather than as one high-water mark per
-- partition: a held frame lies below the newest offset silver holds, and silver must read it
-- again the build its symbol resolves. A model cannot read the held table's previous build (it is
-- built from this one, after it), so the frames not yet here are the held ones plus the new ones.
-- A bronze batch published late is in bronze and not here, so it is read; a frame read is never
-- read twice, so a held trade moves in once, and the merge on the contract's key keeps it to one row.
--
-- is_duplicate: the first delivery of a (venue_symbol, trade_id) by (kafka_partition,
-- kafka_offset, trade_ordinal) is the original (a symbol keys one partition, so offset order is
-- the order the capture produced). A build flags its new rows against the rows already here with
-- the same trade id, and re-emits an earlier row whose flag changes: a held frame taken back can
-- be older than a delivery of the same trade already published, which then becomes the duplicate.
-- ponytail: the anti-join reads every key of bronze and of silver on each build, two integer
-- columns; bound it below by the oldest offset not yet here per partition when the soak's run
-- times say so. A frame holding no trade (a subscribe acknowledgement) is re-read every build.
{{ config(materialized='incremental') }}
with frames as (
  select
    b.kafka_partition,
    b.kafka_offset,
    b.recv_ts_ns,
    b.conn_id,
    b.conn_msg_seq,
    json_parse(from_utf8(b.payload)) as frame
  from {{ source('kraken_trades', 'bronze_trade') }} as b
  {% if is_incremental() %}
  where not exists (
    select 1 from {{ this }} as s
    where s.kafka_partition = b.kafka_partition and s.kafka_offset = b.kafka_offset
  )
  {% endif %}
),

trades as (
  select
    f.kafka_partition,
    f.kafka_offset,
    cast(t.ordinal as integer) as trade_ordinal,
    cast(at_timezone(from_iso8601_timestamp_nanos(json_extract_scalar(t.trade, '$.timestamp')), 'UTC') as timestamp(6) with time zone) as trade_ts,
    cast(json_extract(t.trade, '$.trade_id') as bigint) as trade_id,
    json_extract_scalar(t.trade, '$.symbol') as venue_symbol,
    json_extract_scalar(t.trade, '$.side') as side,
    -- JSON to DECIMAL reads the number's digits, never a double, so nothing is rounded
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
),

resolved as (
  select
    t.*,
    v.instrument_id,
    bool_and(v.instrument_id is not null) over (partition by t.kafka_partition, t.kafka_offset) as frame_resolves
  from trades as t
  left join {{ k2_input('security_master', 'venue_symbol') }} as v
    on v.venue = 'kraken'
    and v.venue_symbol = t.venue_symbol
    and t.trade_ts >= v.valid_from
    and t.trade_ts < v.valid_to
),

arrived as (
  select
    kafka_partition, kafka_offset, trade_ordinal, trade_ts, trade_id, venue_symbol, instrument_id, side,
    price, quantity, order_type, frame_type, received_at, conn_id, conn_msg_seq,
    true as is_new,
    cast(null as boolean) as was_duplicate
  from resolved
  where frame_resolves
),

deliveries as (
  select * from arrived
  {% if is_incremental() %}
  union all
  -- the rows already here that share a trade id with an arriving row
  select
    s.kafka_partition, s.kafka_offset, s.trade_ordinal, s.trade_ts, s.trade_id, s.venue_symbol, s.instrument_id, s.side,
    s.price, s.quantity, s.order_type, s.frame_type, s.received_at, s.conn_id, s.conn_msg_seq,
    false as is_new,
    s.is_duplicate as was_duplicate
  from {{ this }} as s
  where exists (select 1 from arrived as a where a.venue_symbol = s.venue_symbol and a.trade_id = s.trade_id)
  {% endif %}
),

flagged as (
  select
    d.*,
    row_number() over (
      partition by venue_symbol, trade_id
      order by kafka_partition, kafka_offset, trade_ordinal
    ) > 1 as is_duplicate
  from deliveries as d
)

select
  kafka_partition,
  kafka_offset,
  trade_ordinal,
  trade_ts,
  trade_id,
  venue_symbol,
  instrument_id,
  side,
  price,
  quantity,
  order_type,
  frame_type,
  is_duplicate,
  received_at,
  conn_id,
  conn_msg_seq
from flagged
where is_new or is_duplicate <> was_duplicate
