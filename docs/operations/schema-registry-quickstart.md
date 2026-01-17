# Schema Registry Quick Start

**Last Updated**: 2026-01-15

## Overview

The K2 Platform uses Confluent Schema Registry to manage Avro schemas for Kafka topics. Schemas are **automatically registered** when you start Docker services with `make docker-up`.

---

## Automatic Schema Registration

When you run:
```bash
make docker-up
```

The startup sequence:
1. Starts all Docker services
2. Waits for Schema Registry to be healthy
3. **Automatically registers all Avro schemas**
4. Displays service URLs

This ensures schemas are always available when services start.

---

## Manual Schema Registration

If you need to manually register or re-register schemas:

```bash
# Register all schemas
make docker-register-schemas
```

This will register 6 schemas:
- `market.equities.trades-value`
- `market.equities.quotes-value`
- `market.equities.reference_data-value`
- `market.crypto.trades-value`
- `market.crypto.quotes-value`
- `market.crypto.reference_data-value`

---

## Verify Schemas

Check registered schemas:

```bash
# List all subjects
curl -s http://localhost:8081/subjects | python3 -m json.tool

# Get specific schema
curl -s http://localhost:8081/subjects/market.crypto.trades-value/versions/latest \
  | python3 -m json.tool
```

Expected output:
```json
[
    "market.crypto.quotes-value",
    "market.crypto.reference_data-value",
    "market.crypto.trades-value",
    "market.equities.quotes-value",
    "market.equities.reference_data-value",
    "market.equities.trades-value"
]
```

---

## Troubleshooting

### Schema Not Found Error

**Symptom**:
```
ERROR: Subject 'market.crypto.trades-value' not found (HTTP 404)
```

**Cause**: Schema Registry was restarted or schemas were never registered

**Fix**:
```bash
make docker-register-schemas
```

### Schema Registry Not Ready

**Symptom**:
```
Schema Registry failed to start
```

**Fix**:
```bash
# Check Schema Registry logs
make docker-logs-schema

# Restart services
make docker-restart
```

### Connection Refused

**Symptom**:
```
Failed to connect to Schema Registry at http://localhost:8081
```

**Fix**:
```bash
# Check if Schema Registry is running
docker ps | grep schema-registry

# If not running, start services
make docker-up
```

---

## Schema Files Location

Avro schema files are located in:
```
src/k2/schemas/
├── trade_v2.avsc          # V2 trade schema (industry-standard)
├── quote_v2.avsc          # V2 quote schema
├── reference_data_v2.avsc # V2 reference data schema
├── trade.avsc             # V1 trade schema (legacy)
├── quote.avsc             # V1 quote schema (legacy)
└── reference_data.avsc    # V1 reference data schema (legacy)
```

---

## Programmatic Registration

Register schemas from Python code:

```python
from k2.schemas import register_schemas

# Register all schemas (V2 by default)
schema_ids = register_schemas('http://localhost:8081')
print(f"Registered {len(schema_ids)} schemas")

# Register V1 schemas (legacy)
schema_ids_v1 = register_schemas('http://localhost:8081', version='v1')
```

---

## Related Documentation

- [Schema Registry API Reference](https://docs.confluent.io/platform/current/schema-registry/develop/api.html)
- [Avro Schema Evolution](https://docs.confluent.io/platform/current/schema-registry/avro.html)
- [Data Dictionary](../reference/data-dictionary.md) - Field definitions
- [CI/CD Quick Start](ci-cd-quickstart.md) - Full startup process

---

**Note**: Schema Registry data is persisted in Docker volumes. Running `make docker-clean` will delete all schemas.
