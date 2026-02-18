# Docker Compose Archive

This directory contains historical Docker Compose files that have been superseded by the consolidated `docker-compose.v2.yml` at the project root.

## Archived Files

### Phase 5 Files (Archived 2026-02-12)
- `docker-compose.phase5-consolidated.yml` - Complete Phase 5 stack with Iceberg offload
- `docker-compose.phase5-iceberg.yml` - Phase 5 Iceberg-specific services
- `docker-compose.phase5-prefect.yml` - Phase 5 Prefect orchestration services
- `docker-compose.feed-handlers.yml` - Standalone feed handler configuration

## Current State

**Active File**: `/docker-compose.v2.yml` (project root)

This is now the single source of truth for all K2 platform services. All phase 5 services have been merged into this consolidated file.

### Consolidation Details (2026-02-12)
- Merged all phase 5 services (MinIO, Prefect, Spark/Iceberg) into v2
- Kept ClickHouse v26.1 (newer version)
- Merged both config approaches (config files + DDL init scripts)
- Updated ClickHouse native port to 9002 (avoids MinIO port conflict)
- Total resources: 15.0 CPU / 21.25 GB across 12 services

## Migration Notes

If you need to reference old configurations:
1. These files are for historical reference only
2. Do not use them to start services
3. All services should be started via `docker-compose.v2.yml`

## See Also
- Project root: `/docker-compose.v2.yml`
- Architecture docs: `/docs/architecture/`
- Phase documentation: `/docs/phases/`
