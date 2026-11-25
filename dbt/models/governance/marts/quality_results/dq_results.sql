-- Every quality result the platform records, in one shape (ADR-046): the lake jobs' checks
-- from lake.audit.checks, and every data test and unit test each domain's dbt runs recorded.
-- A view, as current as its inputs. Definer security (dbt_project.yml): a reader reads the
-- base tables as the view's owner, dbt_governance, whom rules.json lets read every catalog.
--
-- status is pass, fail, warn or error in both halves:
-- * a lake check: passed is pass; a check that raised is error (maintenance.run_audits files
--   it as not passed, observed -1, detail "check raised <exception>"); any other is fail.
-- * a dbt test: dbt's own pass, fail and warn; error, and skipped as error, because a skipped
--   test never ran: a node upstream of it failed in the same invocation.
-- domain is whose record it is, not what it is about: for a dbt test, the domain whose writer
-- ran it; for a lake check, market-capture, whose product lake-audit holds lake.audit.checks
-- (contracts/market-capture/lake-audit/). subject says what was checked.
--
-- One row per (source_system, checked_at, subject, check_name), the contract's grain. A lake
-- job can file one check more than once under one key: rebuild.py takes one run_ts for a whole
-- rebuild and bronze.decode files bronze_parity and unresolvable_schema_id once per venue and
-- day. Such a group is one row here: its worst status (error, then fail, then pass), the detail
-- of one of its worst rows, and "<n> rows, the worst shown" in front; lake.audit.checks keeps
-- every row.
--
-- Left out of the lake half: silver_flags, which publishes rates and cannot fail
-- (maintenance.INFORMATIONAL), and the rows that are not checks of the platform's data: jobs
-- operator (a purge, an acknowledgement), verify, and replay (scripts/replay-lake.sh's
-- reproducibility record of a research run). A replay filed by hand with record_check.py
-- --failed is left out too: it says a run of the capture binary over archived frames did not
-- complete, which is about that run, not about a table.
--
-- Not in the dbt half: the tests of a node Airflow never started. dbt_marts runs one dbt
-- invocation per node, and a node whose upstream task failed is upstream_failed and runs no
-- dbt, so its tests leave no row at all. A test with no recent row is that signal (dbt/README.md).
{{ config(materialized='view') }}

with lake_rows as (
    select
        run_ts,
        scope,
        check_name,
        case
            when passed then 'pass'
            when observed = -1 and detail like 'check raised %' then 'error'
            else 'fail'
        end as status,
        nullif(concat_ws('; ', 'observed ' || cast(observed as varchar), nullif(detail, '')), '') as detail
    from {{ ref('stg_lake__audit_checks') }}
    where job in ('maintenance', 'ingest') and check_name <> 'silver_flags'
),

lake_ranked as (
    select
        run_ts,
        scope,
        check_name,
        status,
        detail,
        count(*) over (partition by run_ts, scope, check_name) as folded,
        row_number() over (
            partition by run_ts, scope, check_name
            order by case status when 'error' then 0 when 'fail' then 1 else 2 end, detail
        ) as rank_in_key
    from lake_rows
),

lake_checks as (
    select
        run_ts as checked_at,
        'lake_audit' as source_system,
        'market-capture' as domain,
        scope as subject,
        check_name,
        status,
        case
            when folded > 1 then concat_ws('; ', cast(folded as varchar) || ' rows, the worst shown', detail)
            else detail
        end as detail
    from lake_ranked
    where rank_in_key = 1
),

dbt_tests as (
    select
        run_started_at as checked_at,
        'dbt' as source_system,
        domain,
        coalesce(tested, unique_id) as subject,
        test_name as check_name,
        case when status in ('pass', 'fail', 'warn') then status else 'error' end as status,
        case
            when status = 'skipped' then concat_ws(': ', 'skipped, a node upstream of it failed', message)
            else message
        end as detail
    from {{ ref('stg_dq__dbt_test_results') }}
)

select
    checked_at,
    source_system,
    domain,
    subject,
    check_name,
    status,
    detail
from lake_checks
union all
select
    checked_at,
    source_system,
    domain,
    subject,
    check_name,
    status,
    detail
from dbt_tests
