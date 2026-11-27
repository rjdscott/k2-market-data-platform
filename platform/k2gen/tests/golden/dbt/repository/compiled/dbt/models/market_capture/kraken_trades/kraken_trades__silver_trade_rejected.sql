-- What this does and why: the rejected table. Every bronze trade frame that silver_trade does not
-- hold after this build's merge and that the model cannot type: a payload that is not JSON, a trade
-- frame with no data array or no type, or a trade lacking a field silver_trade requires or holding
-- one that does not cast. Each test is the very expression silver_trade and silver_trade_unresolved
-- type the field with, under try() or try_cast, so the three agree on every frame and deciding a
-- reason never raises: a frame silver_trade leaves out as untypeable is here, never lost. One bad
-- trade rejects its frame; the reason is the frame's own, else its first bad trade's by ordinal. A
-- frame that is JSON but not of the trade channel (a subscribe acknowledgement) is no trade frame
-- and is not rejected. Rebuilt whole on every build, like the held table: the frame's bytes and the
-- model do not change between builds, so a rejected frame stays until a release whose model types
-- it builds, and its warn test rejected_frames_zero reports it on each build; the rest publishes.
-- The envelope's columns are not tested here: the bronze audit's parses check holds them.
-- ponytail: silver_trade re-reads a rejected frame each build, as it does a held one: it reads no
-- table of its product but its own (this one is built from it), so the frame is typed again and
-- left out; bound it with the held frames' bound when the soak's run times say so.
{{ config(materialized='table') }}
with frames as (
  select
    b.kafka_partition,
    b.kafka_offset,
    b.kafka_timestamp,
    b.payload,
    try(json_parse(from_utf8(b.payload))) as frame
  from {{ source('kraken_trades', 'bronze_trade') }} as b
  where not exists (
    select 1 from {{ ref('kraken_trades__silver_trade') }} as s
    where s.kafka_partition = b.kafka_partition and s.kafka_offset = b.kafka_offset
  )
),

trades as (
  select
    f.kafka_partition,
    f.kafka_offset,
    t.ordinal,
    case
      when json_extract_scalar(t.trade, '$.timestamp') is null then 'field_missing:timestamp'
      when try(cast(at_timezone(from_iso8601_timestamp_nanos(json_extract_scalar(t.trade, '$.timestamp')), 'UTC') as timestamp(6) with time zone)) is null
        then 'field_not_timestamp:timestamp'
      when json_extract_scalar(t.trade, '$.trade_id') is null then 'field_missing:trade_id'
      when try_cast(json_extract(t.trade, '$.trade_id') as bigint) is null then 'field_not_numeric:trade_id'
      when json_extract_scalar(t.trade, '$.symbol') is null then 'field_missing:symbol'
      when json_extract_scalar(t.trade, '$.side') is null then 'field_missing:side'
      when json_extract_scalar(t.trade, '$.price') is null then 'field_missing:price'
      when try_cast(json_extract(t.trade, '$.price') as decimal(38, 18)) is null then 'field_not_numeric:price'
      when json_extract_scalar(t.trade, '$.qty') is null then 'field_missing:qty'
      when try_cast(json_extract(t.trade, '$.qty') as decimal(38, 18)) is null then 'field_not_numeric:qty'
    end as reason
  from frames as f
  cross join unnest(try(cast(json_extract(f.frame, '$.data') as array(json)))) with ordinality as t (trade, ordinal)
  where json_extract_scalar(f.frame, '$.channel') = 'trade'
),

first_bad as (
  select kafka_partition, kafka_offset, min_by(reason, ordinal) as reason
  from trades
  where reason is not null
  group by kafka_partition, kafka_offset
),

classified as (
  select
    f.kafka_partition,
    f.kafka_offset,
    f.kafka_timestamp,
    f.payload,
    case
      when f.frame is null then 'payload_not_json'
      when json_extract_scalar(f.frame, '$.channel') is distinct from 'trade' then null
      when try(cast(json_extract(f.frame, '$.data') as array(json))) is null then 'no_trade_array'
      when json_extract_scalar(f.frame, '$.type') is null then 'field_missing:type'
      else b.reason
    end as reason
  from frames as f
  left join first_bad as b
    on b.kafka_partition = f.kafka_partition and b.kafka_offset = f.kafka_offset
)

select kafka_partition, kafka_offset, kafka_timestamp, reason, payload
from classified
where reason is not null
