-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-- K2 Market Data Platform - Iceberg Catalog Schema (PostgreSQL)
-- Purpose: Initialize PostgreSQL database for Iceberg REST catalog
-- Version: v2.0
-- Last Updated: 2026-02-11
-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

-- ============================================================================
-- Create Iceberg Catalog Database
-- ============================================================================

CREATE DATABASE IF NOT EXISTS iceberg_catalog
    WITH
    OWNER = postgres
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.utf8'
    LC_CTYPE = 'en_US.utf8'
    TEMPLATE = template0;

\c iceberg_catalog;

-- ============================================================================
-- Create Iceberg Namespace (Catalog Metadata)
-- ============================================================================

-- Iceberg REST catalog uses PostgreSQL to store table metadata
-- The actual table format is managed by Apache Iceberg libraries
-- Tables are created via Spark SQL using Iceberg DDL (see 02-bronze-tables.sql)

-- Note: Iceberg REST catalog automatically creates these tables:
-- - iceberg_tables: Table metadata and schema history
-- - iceberg_snapshots: Snapshot metadata for time-travel
-- - iceberg_manifests: File-level metadata for data files
-- - iceberg_namespace_properties: Namespace configuration

COMMENT ON DATABASE iceberg_catalog IS 'Apache Iceberg REST catalog metadata storage for K2 Market Data Platform cold tier';

-- ============================================================================
-- Verification
-- ============================================================================

-- After Iceberg REST service starts, verify catalog is accessible:
-- SELECT * FROM iceberg_tables;
