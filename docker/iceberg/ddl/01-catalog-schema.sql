-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Iceberg Catalog Namespace
-- Purpose: Create the 'cold' namespace in the Iceberg Hadoop catalog
-- Execution: spark-sql against catalog 'k2' (see 00-run-all-ddl.sh)
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- Catalog config (must match docker/offload/offload_generic.py):
--   spark.sql.catalog.k2       = org.apache.iceberg.spark.SparkCatalog
--   spark.sql.catalog.k2.type  = hadoop
--   spark.sql.catalog.k2.warehouse = /home/iceberg/warehouse
--   spark.sql.defaultCatalog   = k2
--
-- With defaultCatalog=k2, 'cold.<table>' resolves to k2.cold.<table>, which is
-- exactly the target name the offload job writes to.

CREATE NAMESPACE IF NOT EXISTS cold;
