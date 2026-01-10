# Step 12: API Layer - REST API with FastAPI

**Status**: ⬜ Not Started
**Assignee**: TBD
**Started**: -
**Completed**: -
**Estimated Time**: 4-6 hours
**Actual Time**: - hours

## Dependencies
- **Requires**: Step 9 (Query Engine), Step 10 (Replay Engine)
- **Blocks**: Step 13 (Metrics endpoint extends API)

## Goal
Expose query functionality via REST API. Enable integration with dashboards, notebooks, and external systems.

---

## Implementation

### 12.1 Implement FastAPI Server

**File**: `src/k2/api/main.py`

See original plan lines 2579-2737 for complete implementation.

Endpoints:
- `GET /` - Health check
- `GET /trades` - Query trades with filters
- `GET /summary/{symbol}/{date}` - Daily OHLCV
- `GET /snapshots` - List Iceberg snapshots

Features:
- Pydantic models for type safety
- Automatic OpenAPI docs
- CORS middleware
- Error handling with proper HTTP codes

### 12.2 Test API

**File**: `tests/integration/test_api.py`

### 12.3 Run API Server

Update Makefile:
```makefile
.PHONY: api
api:
    uvicorn k2.api.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Validation Checklist

- [ ] API implemented (`src/k2/api/main.py`)
- [ ] Server starts: `make api`
- [ ] Swagger UI accessible: http://localhost:8000/docs
- [ ] All endpoints respond correctly
- [ ] Integration tests pass
- [ ] Response times < 5 seconds
- [ ] Error responses have proper HTTP codes
- [ ] CORS headers present

---

## Rollback Procedure

1. **Stop API server**:
   ```bash
   pkill -f "uvicorn k2.api.main"
   ```

2. **Remove API code**:
   ```bash
   rm -rf src/k2/api/
   rm tests/integration/test_api.py
   ```

3. **Remove Makefile target**:
   ```bash
   git checkout Makefile
   ```

---

## Notes & Decisions

### Decisions Made
- **FastAPI**: Modern framework with auto-docs and async support
- **Pydantic models**: Ensure response validation
- **CORS enabled**: Allow browser clients

### References
- FastAPI: https://fastapi.tiangolo.com/
- Pydantic models: https://docs.pydantic.dev/
