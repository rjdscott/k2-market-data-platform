-- PostgreSQL Initialization Script for Iceberg Catalog
-- Creates necessary extensions and initial schema

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create schema for Iceberg metadata (if not using default public schema)
-- CREATE SCHEMA IF NOT EXISTS iceberg;

-- Grant permissions (adjust based on your security requirements)
-- In production, create separate read-only and read-write roles
GRANT ALL PRIVILEGES ON DATABASE iceberg_catalog TO iceberg;

-- Create audit log table (for governance)
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_id VARCHAR(255),
    action VARCHAR(50),  -- CREATE, UPDATE, DELETE, QUERY
    resource_type VARCHAR(50),  -- TABLE, NAMESPACE, SNAPSHOT
    resource_name VARCHAR(255),
    metadata JSONB,
    ip_address INET,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_resource ON audit_log(resource_type, resource_name);

-- Create table for RBAC (Role-Based Access Control)
CREATE TABLE IF NOT EXISTS user_roles (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,  -- admin, writer, reader
    namespace VARCHAR(255),  -- NULL for global roles
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by VARCHAR(255),
    UNIQUE(user_id, namespace)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);

-- Create table for data lineage tracking
CREATE TABLE IF NOT EXISTS data_lineage (
    id SERIAL PRIMARY KEY,
    source_table VARCHAR(255),
    target_table VARCHAR(255),
    transformation_type VARCHAR(50),  -- KAFKA_INGEST, AGGREGATION, JOIN
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_lineage_source ON data_lineage(source_table);
CREATE INDEX IF NOT EXISTS idx_lineage_target ON data_lineage(target_table);

-- Insert default admin user (for development only - remove in production)
INSERT INTO user_roles (user_id, role, namespace, created_by)
VALUES ('admin', 'admin', NULL, 'system')
ON CONFLICT DO NOTHING;

-- Log initialization
INSERT INTO audit_log (user_id, action, resource_type, resource_name, metadata)
VALUES ('system', 'CREATE', 'DATABASE', 'iceberg_catalog', '{"event": "database_initialized"}'::jsonb);
