-- lake.gold.ohlcv_1m without the newest 20 minutes: ADR-035's delayed product, a view, so it
-- is as current as the lake and needs no schedule of its own.
--
-- docker/lake/gold.py MERGEs every minute an ingest batch touches, recomputed over all of
-- gold.trades, so a minute the lake has only partly seen is rewritten when its later trades
-- land at the next ingest. Two cuts keep such a minute out:
--   1. the delay: the whole minute, [window_start, window_start + 1 minute), ended at least
--      20 minutes before the query, so no trade in it is younger than 20 minutes;
--   2. the horizon: the minute is older than the venue's newest minute in the lake, which
--      can still grow. A stalled ingest stops the horizon, and with it the view.
-- Both on window_start alone, so the first also prunes the table's month partitions. The
-- horizon is per venue: a venue whose capture or ingest lags holds back only its own
-- candles. Not covered: a trade delivered after its minute was passed (the view then shows
-- the lake's corrected candle), and partitions read to different points by a capped
-- catch-up ingest (lake_ingest's max_offsets_per_partition).
{{ config(materialized='view') }}

with horizon as (
    select
        exchange,
        max(window_start) as newest_minute
    from {{ ref('stg_gold__ohlcv_1m') }}
    group by 1
)

select
    c.exchange,
    c.canonical_symbol,
    c.window_start,
    c.open_e8,
    c.high_e8,
    c.low_e8,
    c.close_e8,
    c.volume,
    c.quote_volume,
    c.trade_count,
    c.open_time,
    c.close_time
from {{ ref('stg_gold__ohlcv_1m') }} as c
inner join horizon as h on c.exchange = h.exchange
where
    c.window_start <= {{ current_timestamp() }} - interval '21' minute
    and c.window_start < h.newest_minute
