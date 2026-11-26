-- Synthetic rows (symbols SYN1 to SYN5, made-up values), run as dbt_governance by run.sh.
CREATE SCHEMA governance.spike_acl_20261126;
USE governance.spike_acl_20261126;
CREATE TABLE t (id bigint, sym varchar, secret varchar, hidden varchar, price double, ts timestamp(6) with time zone);
INSERT INTO t VALUES
 (1,'SYN1','alpha','h1',100, current_timestamp - INTERVAL '2' HOUR),
 (2,'SYN2','bravo','h2',200, current_timestamp - INTERVAL '1' HOUR),
 (3,'SYN3','charlie','h3',300, current_timestamp - INTERVAL '30' MINUTE);
INSERT INTO t VALUES
 (4,'SYN4','delta','h4',400, current_timestamp),
 (5,'SYN5','echo','h5',500, current_timestamp + INTERVAL '1' HOUR);
CREATE TABLE k (id bigint, secret varchar);
INSERT INTO k VALUES (1,'alpha'),(2,'bravo'),(3,'charlie'),(4,'delta'),(5,'echo');
CREATE VIEW v_definer SECURITY DEFINER AS SELECT id, sym, secret, hidden, price, ts FROM t;
CREATE VIEW v_invoker SECURITY INVOKER AS SELECT id, sym, secret, price, ts FROM t;
CREATE VIEW v_ruled SECURITY DEFINER AS SELECT id, sym, secret, price, ts FROM t;
SELECT id, secret, ts FROM t ORDER BY id;
SELECT snapshot_id, operation FROM "t$snapshots" ORDER BY committed_at;
