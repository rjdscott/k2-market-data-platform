# Step 01: E2E Infrastructure Setup

**Duration**: 4 hours
**Status**: 🟡 In Progress
**Priority**: High

---

## Objectives

Establish the foundation for end-to-end testing by creating test framework structure, Docker management utilities, and pytest configuration.

---

## Detailed Tasks

### Task 1.1: Directory Structure Creation (30 minutes)

**Files to Create**:
```
tests/e2e/
├── __init__.py
├── conftest.py              # E2E fixtures and utilities
├── test_complete_pipeline.py  # Main data flow validation
├── test_docker_stack.py      # Docker orchestration tests
├── test_data_freshness.py    # Performance and latency tests
└── utils/
    ├── __init__.py
    ├── docker_manager.py      # Docker Compose utilities
    ├── data_validator.py      # Data consistency validation
    └── performance_monitor.py # Latency measurement utilities
```

**Implementation Notes**:
- Follow existing test structure patterns from `tests/integration/`
- Ensure proper Python package structure with `__init__.py` files
- Add type hints for all functions and classes
- Include comprehensive docstrings following Google style

### Task 1.2: Pytest Configuration Updates (30 minutes)

**File**: `pyproject.toml`

**Changes Required**:
```toml
# Add to markers list (line ~322)
"e2e: End-to-end tests (require full Docker stack, slow)",

# Update addopts (line ~314) to exclude e2e by default
"-m", "not slow and not chaos and not soak and not operational and not e2e",
```

**Implementation Notes**:
- E2E tests are slow and resource-intensive, exclude by default
- Explicit opt-in required with `-m e2e` flag
- Follow existing marker pattern and formatting
- Test configuration with `pytest --collect-only` to verify

### Task 1.3: Docker Management Utilities (90 minutes)

**File**: `tests/e2e/utils/docker_manager.py`

**Key Functions**:
```python
import asyncio
import docker
from typing import Dict, List, Optional
from pathlib import Path

class E2EDockerManager:
    """Manage Docker Compose for E2E tests with proper lifecycle management."""
    
    def __init__(self, compose_file: str = "docker-compose.v1.yml"):
        """Initialize Docker manager with compose file path."""
        self.compose_file = Path(compose_file)
        self.docker_client = docker.from_env()
        self.running_services: Dict[str, str] = {}
        
    async def start_minimal_stack(self) -> Dict[str, str]:
        """Start minimal stack: Kafka, Schema Registry, MinIO, PostgreSQL, Iceberg REST."""
        
    async def start_full_stack(self) -> Dict[str, str]:
        """Start full stack including k2-query-api and consumer-crypto."""
        
    async def stop_stack(self) -> None:
        """Stop and clean up all services with proper error handling."""
        
    async def wait_for_health(self, service_name: str, timeout: int = 60) -> bool:
        """Wait for service health check with timeout."""
        
    async def get_service_logs(self, service_name: str, lines: int = 50) -> str:
        """Get recent logs for debugging failed tests."""
```

**Implementation Notes**:
- Use `docker` Python package for container management
- Implement proper error handling and retry logic
- Add comprehensive logging for debugging test failures
- Support both minimal and full stack configurations

### Task 1.4: E2E Test Fixtures (90 minutes)

**File**: `tests/e2e/conftest.py`

**Key Fixtures**:
```python
import asyncio
import pytest
import httpx
from typing import AsyncGenerator, Dict

from .utils.docker_manager import E2EDockerManager

@pytest.fixture(scope="session")
async def docker_manager() -> AsyncGenerator[E2EDockerManager, None]:
    """Provide Docker manager for E2E tests with session-scoped lifecycle."""
    manager = E2EDockerManager()
    yield manager
    await manager.stop_stack()

@pytest.fixture(scope="session")
async def minimal_stack(docker_manager: E2EDockerManager) -> AsyncGenerator[Dict[str, str], None]:
    """Start minimal Docker stack for E2E tests."""
    services = await docker_manager.start_minimal_stack()
    yield services
    await docker_manager.stop_stack()

@pytest.fixture(scope="session")
async def full_stack(docker_manager: E2EDockerManager) -> AsyncGenerator[Dict[str, str], None]:
    """Start full Docker stack for comprehensive E2E tests."""
    services = await docker_manager.start_full_stack()
    yield services
    await docker_manager.stop_stack()

@pytest.fixture(scope="function")
async def api_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """HTTP client for API testing with authentication."""
    async with httpx.AsyncClient(
        base_url="http://localhost:8000",
        headers={"X-API-Key": "k2-dev-api-key-2026"},
        timeout=30.0
    ) as client:
        yield client
```

**Implementation Notes**:
- Session-scoped fixtures for expensive Docker operations
- Function-scoped fixtures for test isolation (API client)
- Proper cleanup and resource management
- Integration with existing test infrastructure
- Support for both minimal and full stack scenarios

---

## Success Criteria

### Must-Have Requirements
- ✅ E2E directory structure created following Python package conventions
- ✅ Pytest configuration updated with e2e marker and exclusion
- ✅ Docker manager utilities implemented with comprehensive functionality
- ✅ Base fixtures created and tested for proper lifecycle management

### Nice-to-Have Requirements
- ✅ Resource monitoring capabilities in Docker manager
- ✅ Advanced logging and debugging utilities
- ✅ Support for both minimal and full stack configurations

---

## Testing the Implementation

### Validation Steps
1. **Directory Structure**: Verify all files created with proper Python package structure
2. **Pytest Integration**: Run `pytest --collect-only -m e2e` to verify marker registration
3. **Docker Manager**: Test basic functionality with manual Docker operations
4. **Fixture Testing**: Create simple test to verify fixtures work correctly

### Expected Behaviors
- E2E tests can be run with `pytest -m e2e`
- Docker services can be started and stopped programmatically
- Resource cleanup happens automatically after tests
- No conflicts with existing integration tests

---

## Common Issues and Solutions

### Docker Permission Issues
**Problem**: Permission denied when accessing Docker socket
**Solution**: Ensure user is in docker group or run with appropriate permissions

### Resource Leaks
**Problem**: Services not stopping properly
**Solution**: Implement proper cleanup in fixture teardown with timeout handling

### Port Conflicts
**Problem**: Services fail to start due to port conflicts
**Solution**: Ensure clean environment and check for existing containers before starting

---

## Next Steps

After completing Step 01:
1. **Verify Infrastructure**: Run `pytest -m e2e --collect-only` to confirm setup
2. **Document Usage**: Create examples for team members
3. **Proceed to Step 02**: Begin pipeline validation implementation

---

**Implementation Order**: Task 1.1 → Task 1.2 → Task 1.3 → Task 1.4
**Estimated Completion**: 4 hours
**Dependencies**: None (can be done independently)

---

**Status**: 🟡 Documentation complete, implementation starting
**Started**: 2026-01-17
**Current Task**: Task 1.1 - Directory Structure Creation