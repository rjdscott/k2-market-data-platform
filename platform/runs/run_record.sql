-- The run-record table (phase 001 interface A6, docs/development/phase-001-interfaces.md).
-- Frozen here. Step S15's lake emitter copies this file into every release as
-- compiled/lake/ddl/trino/governance.run_records.run_record.sql (k2gen.model.ddl_path), and
-- deploy (step S19) applies it when the release's needs.ddl names the table. Written from
-- phase 002 by the dbt wrapper, the Airflow record task and the Spark publish, always as
-- svc.runs through the governance_build catalog. Append-only: a row is never updated or deleted.
--
-- History, not authority: a dbt output's tested snapshot is its `audited` tag, a bronze or
-- platform table's is its `main` (k2gen.model.tested_ref; one query, A6:
-- SELECT snapshot_id FROM <cat>.<ns>."<t>$refs" WHERE name = 'audited').
-- The catalog drift check holds every `audited` tag equal to the newest `audited` row
-- of its table (ADR-061 part 6).
--
-- Each column's meaning is its COMMENT, so Trino's information_schema and the catalog
-- carry it too; tests/test_frozen_interfaces.py fails on a column without one.
CREATE TABLE governance_build.run_records.run_record (
    run_id varchar COMMENT 'The Airflow run: <dag_id>/<run_id>, the dag_id being the product fence',
    product_id varchar COMMENT 'The product: <domain>.<product>, as in its ODPS document',
    release_id varchar COMMENT 'The release the run resolved once at its start: <tag>-<sha12> (A1)',
    task varchar COMMENT 'The step that wrote the row: fetch, load, audit, publish, build, expire, record or maintain',
    event varchar COMMENT 'What happened: started, passed, failed, rolled_back, audited, reconciled or expired',
    table_name varchar COMMENT 'The table the row is about, fully qualified (<catalog>.<namespace>.<table>); null for a row about the whole task',
    snapshot_before bigint COMMENT 'The table''s main snapshot id saved before the step; the id a failed build is restored to',
    snapshot_after bigint COMMENT 'The table''s main snapshot id after the step; on an audited row the id the tag or main now names',
    inputs varchar COMMENT 'JSON array, one object per input read: {contractId, relation, snapshot, state, lagMinutes}; null when the step read none',
    rows_written bigint COMMENT 'Rows the step reports it wrote; null when it reports none',
    bytes_written bigint COMMENT 'Bytes the step reports it wrote; null when it reports none',
    started_at timestamp(6) with time zone COMMENT 'When the step started, UTC',
    recorded_at timestamp(6) with time zone COMMENT 'When this row was written, UTC; the partition source',
    duration_s double COMMENT 'Seconds from started_at to recorded_at, as the writer measured them',
    detail varchar COMMENT 'Free text for an operator, at most 1000 characters (cut as dbt/macros/record_test_results.sql cuts messages); never a secret',
    dbt_invocation_id varchar COMMENT 'dbt''s invocation_id on a build row; the results hook writes it and run_id on every test row, so a publish joins its tests; null when dbt did not run',
    openlineage_run_id varchar COMMENT 'The OpenLineage run id the step emitted lineage under, a UUID; null while no lineage is emitted'
)
COMMENT 'One row per step of every product run (phase 001 interface A6); written only by svc.runs'
WITH (partitioning = ARRAY['day(recorded_at)'])
