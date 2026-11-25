-- Per venue and UTC day, how complete the lake is. From gold.trades: what the venue's own
-- trade ids say about trades the archive never received (seq_gap, missing_before, computed
-- per symbol in silver). From audit.checks: what the lake jobs found that day about the
-- venue's tables and partitions. Rebuilt whole on every run, like ohlcv_1m.
-- Not portable as written: regexp_like is Trino's. Everything else is plain SQL.
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
-- cross-venue scope (lake.gold.trades, lake.gold.bars) is no venue's. Counted: the
-- maintenance audits, but not silver_flags, which publishes rates and cannot fail
-- (maintenance.INFORMATIONAL); and the records an ingest's offset_gap rows say Redpanda
-- evicted before the lake read them. Operator, verify and replay rows are not checks of a day.
audit_days as (
    select
        v.exchange,
        cast(a.run_ts as date) as utc_date,
        sum(case when a.job = 'maintenance' and a.check_name <> 'silver_flags' then 1 else 0 end) as audit_checks,
        sum(
            case when a.job = 'maintenance' and a.check_name <> 'silver_flags' and not a.passed then 1 else 0 end
        ) as audit_checks_failed,
        coalesce(sum(case when a.job = 'ingest' and a.check_name = 'offset_gap' then a.observed end), 0)
            as records_lost
    from {{ ref('stg_audit__checks') }} as a
    inner join venues as v on regexp_like(a.scope, '(^|[._/])' || v.exchange || '($|[._/])')
    group by 1, 2
)

select
    coalesce(t.exchange, a.exchange) as exchange,
    coalesce(t.utc_date, a.utc_date) as utc_date,
    coalesce(t.trades, 0) as trades,
    coalesce(t.trades_after_gap, 0) as trades_after_gap,
    coalesce(t.trade_ids_missing, 0) as trade_ids_missing,
    coalesce(t.trades_gap_unknown, 0) as trades_gap_unknown,
    coalesce(a.audit_checks, 0) as audit_checks,
    coalesce(a.audit_checks_failed, 0) as audit_checks_failed,
    coalesce(a.records_lost, 0) as records_lost
from trade_days as t
full outer join audit_days as a on t.exchange = a.exchange and t.utc_date = a.utc_date
