-- The holds table (step I-7 of phases 004 and 005, docs/development/phase-001-interfaces.md,
-- "Holds"). Frozen here, beside run_record.sql and in its namespace. k2gen's lake emitter copies
-- this file into every release as compiled/lake/ddl/trino/governance.run_records.holds.sql
-- (k2gen.model.ddl_path, model.PLATFORM_SQL), and deploy applies it when the release's needs.ddl
-- names the table. Written only by svc.operator, INSERT through governance_build
-- (platform/runs/k2runs.py: hold, release), from the host alone (scripts/hold.py; its password file
-- is mounted into no container); read through governance, read-only at the connector, by
-- svc.operator, svc.catalog, a DAG's resolve as svc.runs and deploy as svc.platform (k2runs.held).
--
-- Append-only: svc.operator holds INSERT and nothing else here, so no row is ever updated or
-- deleted; SELECT through governance_build, the right to roll the table back (spike (e)), is
-- svc.platform's alone, the creator, with OWNERSHIP. A hold is one row with released_at null; its release is a second row
-- that copies the hold's product_id, kind, reason, held_by and held_at and sets released_at and
-- released_by. A hold is open while no release row carries its (product_id, kind, held_at).
-- One open hold per product and kind: an operator hold and a restore's lock are apart, so the
-- end of a restore never ends an operator's hold.
--
-- Each column's meaning is its COMMENT; tests/test_frozen_interfaces.py fails on one without.
CREATE TABLE governance_build.run_records.holds (
    product_id varchar COMMENT 'The product held: <domain>.<product>, as in its ODPS document',
    kind varchar COMMENT 'Who holds it: operator (make hold), rebuild (make rebuild''s per-product lock) or restore (make restore''s, on every product)',
    reason varchar COMMENT 'Why, as the holder gave it, at most 1000 characters; never a secret',
    held_by varchar COMMENT 'Who took the hold: the operator''s name, or the make target for a lock',
    held_at timestamp(6) with time zone COMMENT 'When the hold was taken, UTC; with product_id and kind it names the hold its release row ends',
    released_at timestamp(6) with time zone COMMENT 'Null on the hold row; on the release row, when the hold ended, UTC',
    released_by varchar COMMENT 'Null on the hold row; on the release row, who ended it'
)
COMMENT 'One row per hold taken and one per hold released (step I-7); written only by svc.operator'
