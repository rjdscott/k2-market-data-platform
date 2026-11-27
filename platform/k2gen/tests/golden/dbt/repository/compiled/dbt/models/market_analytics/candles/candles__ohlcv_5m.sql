-- What this does and why: five-minute candles over canonical-trades, read through k2_input at its
-- tested snapshot, a venue's later delivery of a trade (is_duplicate) not counted. A window is
-- [window_start, window_end) in UTC: the trade time is put in UTC before it is truncated, so the
-- bucket never moves with the zone a timestamp carries. The larger timeframes are computed
-- from the trades too, never rolled up from ohlcv_1m: each table finds its touched windows by the
-- same rule on the same input, so a late trade reaches all four in one build and no timeframe
-- depends on another table's merge having run or being right.
--
-- Incremental, which windows a build writes: every window holding a canonical trade written past
-- this table's watermark, its highest through_load_seq (load_seq only grows, so a trade taken
-- back late or flagged again upstream is past it however old its trade_ts). Each such window is
-- recomputed over all its trades and merged on (venue, instrument_id, window_start): a late trade
-- rewrites its candle, never adds to it, as the archived lake's candles were (gold.py). open and
-- close take the order the archived ohlcv_1m.sql used, trade time first and then the capture's own
-- order, here the venue row's key, so a tie at one instant is always broken the same way.
-- ponytail: every build reads all of canonical-trades to find and recompute the touched windows;
-- bound the read below by the earliest touched window_start when the soak's run times say so. A
-- window whose every trade turned duplicate keeps its old row (a merge does not delete); a
-- trade's deliveries share one trade_ts, so its first delivery keeps the window.
{{ config(materialized='incremental') }}
with trades as (
  select
    t.venue,
    t.instrument_id,
    date_add('minute', 5 * (minute(at_timezone(t.trade_ts, 'UTC')) / 5), date_trunc('hour', at_timezone(t.trade_ts, 'UTC'))) as window_start,
    t.trade_ts,
    t.kafka_partition,
    t.kafka_offset,
    t.trade_ordinal,
    t.price,
    t.quantity,
    t.is_duplicate,
    t.load_seq
  from {{ k2_input('canonical_trades', 'trades') }} as t
),

latest as (
  select max(load_seq) as through_load_seq from trades
),

counted as (
  select r.*
  from trades as r
  {% if is_incremental() %}
  join (
    -- the windows holding a trade written past this table's watermark
    select distinct venue, instrument_id, window_start
    from trades
    where load_seq > (select coalesce(max(through_load_seq), 0) from {{ this }})
  ) as k
    on k.venue = r.venue and k.instrument_id = r.instrument_id and k.window_start = r.window_start
  {% endif %}
)

select
  c.venue,
  c.instrument_id,
  c.window_start,
  c.window_start + interval '5' minute as window_end,
  min_by(c.price, row(c.trade_ts, c.kafka_partition, c.kafka_offset, c.trade_ordinal)) as open,
  max(c.price) as high,
  min(c.price) as low,
  max_by(c.price, row(c.trade_ts, c.kafka_partition, c.kafka_offset, c.trade_ordinal)) as close,
  sum(c.quantity) as volume,
  cast(sum(cast(c.price as decimal(30, 12)) * cast(c.quantity as decimal(30, 10))) as decimal(38, 18)) as quote_volume,
  count(*) as trade_count,
  min(c.trade_ts) as first_trade_at,
  max(c.trade_ts) as last_trade_at,
  l.through_load_seq
from counted as c
cross join latest as l
where not c.is_duplicate
group by c.venue, c.instrument_id, c.window_start, l.through_load_seq
