-- Per venue and UTC day, how complete the lake is. From gold.trades: what the venue's own
-- trade ids say about trades the archive never received (seq_gap, missing_before, computed
-- per symbol in silver), by the day of exchange_ts. From audit.checks: what the lake jobs
-- found about the venue's tables and topic partitions. Rebuilt whole on every run.
-- Not portable as written: regexp_like and split_part are Trino's. The rest is plain SQL.
{{ config(tags=['regexp_like']) }}

with trade_days as (
    select
        exchange,
        cast(exchange_ts as date) as utc_date,
        count(*) as trades,
        sum(case when seq_gap then 1 else 0 end) as trades_after_gap,
        coalesce(sum(missing_before), 0) as trade_ids_missing,
        sum(case when seq_gap is null then 1 else 0 end) as trades_gap_unknown
    from {{ ref('stg_gold__trades') }}
    group by 1, 2
),

venues as (
    select distinct exchange from trade_days
),

-- A check is a venue's when its scope names the venue as a whole token: lake.bronze.kraken_trade,
-- lake.silver.trades_kraken, lake.gold.trades/kraken, market.crypto.v3.raw.kraken/7. A
-- cross-venue scope (lake.gold.trades, lake.gold.ohlcv_1m, lake.gold.bars) is no venue's.
-- Counted: the maintenance audits, but not silver_flags, which publishes rates and cannot fail
-- (maintenance.INFORMATIONAL); and the ingest's offset_gap rows. Operator, verify and replay
-- rows are not checks.
--
-- Which day a check is about (docker/lake/maintenance.py):
-- * day-scoped checks look at the day before the run: ohlcv_parity and bars_parity compare the
--   previous UTC day (both cross-venue, so no venue's here), and bronze_schema_drift samples
--   the 24 hours before the run, 21 of them in the previous UTC day at the 03:00 schedule.
--   Filed under that previous day.
-- * every other audit reads the venue's whole table as it stood at the run, so it belongs
--   to no trade day: filed under the day it ran, and named as such.
-- * an offset_gap row records records Redpanda evicted before an ingest read them, filed
--   under the day the ingest skipped them, per topic family (raw frames, trades, book).
audit_rows as (
    select
        v.exchange,
        a.job,
        a.passed,
        a.observed,
        split_part(a.scope, '.', 4) as topic_family,
        a.check_name in ('bronze_schema_drift', 'ohlcv_parity', 'bars_parity') as day_scoped,
        cast(a.run_ts as date) as run_date
    from {{ ref('stg_audit__checks') }} as a
    inner join venues as v on regexp_like(a.scope, '(^|[._/])' || v.exchange || '($|[._/])')
    where
        (a.job = 'maintenance' and a.check_name <> 'silver_flags')
        or (a.job = 'ingest' and a.check_name = 'offset_gap')
),

audit_days as (
    select
        exchange,
        case when day_scoped then run_date - interval '1' day else run_date end as utc_date,
        sum(case when job = 'maintenance' and day_scoped then 1 else 0 end) as day_audits,
        sum(case when job = 'maintenance' and day_scoped and not passed then 1 else 0 end) as day_audits_failed,
        sum(case when job = 'maintenance' and not day_scoped then 1 else 0 end) as whole_table_audits,
        sum(case when job = 'maintenance' and not day_scoped and not passed then 1 else 0 end)
            as whole_table_audits_failed,
        coalesce(sum(case when job = 'ingest' and topic_family = 'raw' then observed end), 0) as raw_frames_lost,
        coalesce(sum(case when job = 'ingest' and topic_family = 'trades' then observed end), 0) as trade_records_lost,
        coalesce(sum(case when job = 'ingest' and topic_family = 'book' then observed end), 0) as book_records_lost
    from audit_rows
    group by 1, 2
)

select
    coalesce(t.exchange, a.exchange) as exchange,
    coalesce(t.utc_date, a.utc_date) as utc_date,
    coalesce(t.trades, 0) as trades,
    coalesce(t.trades_after_gap, 0) as trades_after_gap,
    coalesce(t.trade_ids_missing, 0) as trade_ids_missing,
    coalesce(t.trades_gap_unknown, 0) as trades_gap_unknown,
    coalesce(a.day_audits, 0) as day_audits,
    coalesce(a.day_audits_failed, 0) as day_audits_failed,
    coalesce(a.whole_table_audits, 0) as whole_table_audits,
    coalesce(a.whole_table_audits_failed, 0) as whole_table_audits_failed,
    coalesce(a.raw_frames_lost, 0) as raw_frames_lost,
    coalesce(a.trade_records_lost, 0) as trade_records_lost,
    coalesce(a.book_records_lost, 0) as book_records_lost
from trade_days as t
full outer join audit_days as a on t.exchange = a.exchange and t.utc_date = a.utc_date
