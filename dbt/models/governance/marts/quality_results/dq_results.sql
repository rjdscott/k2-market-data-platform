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
-- Left out of the lake half: silver_flags, which publishes rates and cannot fail
-- (maintenance.INFORMATIONAL), and the rows that are not checks: jobs operator (a purge, an
-- acknowledgement), verify and replay (a reproducibility record).
{{ config(materialized='view') }}

with lake_checks as (
    select
        run_ts as checked_at,
        'lake_audit' as source_system,
        'market-capture' as domain,
        scope as subject,
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
