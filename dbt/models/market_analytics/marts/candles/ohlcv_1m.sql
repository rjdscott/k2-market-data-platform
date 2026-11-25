-- 1-minute OHLCV over lake.gold.trades: the port of gold.candles() in
-- docker/lake/gold.py, compared with lake.gold.ohlcv_1m by scripts/parity_ohlcv.py --dbt.
-- Not portable: min_by, max_by and row comparison are Trino's.
{{ config(
    tags=['min_by_max_by'],
    properties={'partitioning': "ARRAY['exchange', 'month(window_start)']"},
) }}

select
    exchange,
    canonical_symbol,
    -- floor(unix_timestamp / 60) * 60 in Spark; the session zone is UTC (profiles.yml).
    date_trunc('minute', exchange_ts) as window_start,
    -- The total order gold.py breaks ties with: (exchange_ts, recv_ts_ns, trade_seq).
    min_by(price_e8, row(exchange_ts, recv_ts_ns, trade_seq)) as open_e8,
    max(price_e8) as high_e8,
    min(price_e8) as low_e8,
    max_by(price_e8, row(exchange_ts, recv_ts_ns, trade_seq)) as close_e8,
    -- Every arithmetic step casts (ADR-035). qty_e8 / 1e8 has 8 decimals, exact.
    cast(sum(cast(qty_e8 as decimal(38, 0))) as decimal(38, 10)) / 100000000 as volume,
    -- The lake's quote_volume has 6 decimals, rounded half up (dbt/README.md), so
    -- this rounds there too. The verbatim DECIMAL(38,10) product overflows in
    -- Trino; the integer product does not.
    cast(
        cast(sum(cast(price_e8 as decimal(38, 0)) * qty_e8) as decimal(38, 6)) / 10000000000000000
        as decimal(38, 10)
    ) as quote_volume,
    count(*) as trade_count,
    min(exchange_ts) as open_time,
    max(exchange_ts) as close_time
from {{ ref('stg_gold__trades') }}
group by 1, 2, 3
