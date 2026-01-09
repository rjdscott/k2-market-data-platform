.PHONY: help setup install dev-install clean test test-unit test-integration \
        test-performance coverage lint format type-check quality docker-up \
        docker-down docker-logs docker-clean init-infra simulate docs

# Default target
.DEFAULT_GOAL := help

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Python and environment
PYTHON := python3.11
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
BLACK := $(VENV)/bin/black
ISORT := $(VENV)/bin/isort
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy

# Docker compose
DOCKER_COMPOSE := docker-compose
DOCKER_COMPOSE_FILE := docker-compose.yml

# ==============================================================================
# Help
# ==============================================================================

help: ## Show this help message
	@echo "$(BLUE)K2 Market Data Platform - Development Commands$(NC)"
	@echo ""
	@echo "$(GREEN)Setup & Installation:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; /setup|install|clean/ {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Docker Services:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; /docker/ {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Testing:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; /test|coverage/ {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Code Quality:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; /lint|format|type|quality/ {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Operations:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; /init|simulate|docs/ {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'

# ==============================================================================
# Setup & Installation
# ==============================================================================

setup: ## Complete setup (create venv, install deps, start services, init infra)
	@echo "$(BLUE)Starting complete K2 Platform setup...$(NC)"
	@make install
	@make docker-up
	@echo "$(GREEN)Waiting for services to be healthy...$(NC)"
	@sleep 15
	@make init-infra
	@echo "$(GREEN)✓ Setup complete! Run 'make help' for available commands$(NC)"

install: ## Create virtual environment and install dependencies
	@echo "$(BLUE)Creating virtual environment...$(NC)"
	@$(PYTHON) -m venv $(VENV)
	@echo "$(BLUE)Installing dependencies...$(NC)"
	@$(PIP) install --upgrade pip setuptools wheel
	@$(PIP) install -e .
	@echo "$(GREEN)✓ Installation complete$(NC)"

dev-install: ## Install with all development dependencies
	@echo "$(BLUE)Installing with development dependencies...$(NC)"
	@$(PYTHON) -m venv $(VENV)
	@$(PIP) install --upgrade pip setuptools wheel
	@$(PIP) install -e ".[all]"
	@echo "$(GREEN)✓ Development installation complete$(NC)"

clean: ## Remove generated files and caches
	@echo "$(BLUE)Cleaning up...$(NC)"
	@rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@find . -type f -name "*.pyo" -delete
	@find . -type f -name ".coverage" -delete
	@rm -rf htmlcov/ .coverage coverage.xml
	@echo "$(GREEN)✓ Cleanup complete$(NC)"

# ==============================================================================
# Docker Services
# ==============================================================================

docker-up: ## Start all Docker services
	@echo "$(BLUE)Starting Docker services...$(NC)"
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMPOSE_FILE) up -d
	@echo "$(GREEN)✓ Services started. Use 'make docker-logs' to view logs$(NC)"
	@echo "$(YELLOW)Service URLs:$(NC)"
	@echo "  Kafka UI:         http://localhost:8080"
	@echo "  Schema Registry:  http://localhost:8081"
	@echo "  MinIO Console:    http://localhost:9001"
	@echo "  Grafana:          http://localhost:3000"
	@echo "  Prometheus:       http://localhost:9090"

docker-down: ## Stop all Docker services
	@echo "$(BLUE)Stopping Docker services...$(NC)"
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMPOSE_FILE) down
	@echo "$(GREEN)✓ Services stopped$(NC)"

docker-restart: ## Restart all Docker services
	@make docker-down
	@make docker-up

docker-logs: ## Follow logs from all services
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMPOSE_FILE) logs -f

docker-logs-kafka: ## Follow Kafka logs
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMPOSE_FILE) logs -f kafka

docker-logs-schema: ## Follow Schema Registry logs
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMPOSE_FILE) logs -f schema-registry

docker-ps: ## Show status of all services
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMPOSE_FILE) ps

docker-clean: ## Remove all containers, volumes, and networks
	@echo "$(RED)WARNING: This will delete all data in Docker volumes!$(NC)"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		$(DOCKER_COMPOSE) -f $(DOCKER_COMPOSE_FILE) down -v --remove-orphans; \
		echo "$(GREEN)✓ Docker resources cleaned$(NC)"; \
	else \
		echo "$(YELLOW)Aborted$(NC)"; \
	fi

docker-rebuild: ## Rebuild and restart services
	@echo "$(BLUE)Rebuilding services...$(NC)"
	@$(DOCKER_COMPOSE) -f $(DOCKER_COMPOSE_FILE) up -d --build
	@echo "$(GREEN)✓ Services rebuilt$(NC)"

# ==============================================================================
# Testing
# ==============================================================================

test: ## Run all tests
	@echo "$(BLUE)Running all tests...$(NC)"
	@$(PYTEST) tests/ -v
	@echo "$(GREEN)✓ All tests passed$(NC)"

test-unit: ## Run unit tests only (fast, no Docker required)
	@echo "$(BLUE)Running unit tests...$(NC)"
	@$(PYTEST) tests/unit/ -v -m unit
	@echo "$(GREEN)✓ Unit tests passed$(NC)"

test-integration: docker-up ## Run integration tests (requires Docker)
	@echo "$(BLUE)Running integration tests...$(NC)"
	@sleep 5  # Wait for services to be ready
	@$(PYTEST) tests/integration/ -v -m integration
	@echo "$(GREEN)✓ Integration tests passed$(NC)"

test-performance: docker-up ## Run performance benchmarks
	@echo "$(BLUE)Running performance benchmarks...$(NC)"
	@$(PYTEST) tests/performance/ -v -m performance --benchmark-only
	@echo "$(GREEN)✓ Benchmarks complete$(NC)"

test-watch: ## Run tests in watch mode
	@echo "$(BLUE)Running tests in watch mode...$(NC)"
	@$(PYTEST) tests/ -v -f

coverage: ## Run tests with coverage report
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	@$(PYTEST) tests/ --cov=src/k2 --cov-report=term-missing --cov-report=html
	@echo "$(GREEN)✓ Coverage report generated in htmlcov/index.html$(NC)"

coverage-report: ## Open coverage report in browser
	@open htmlcov/index.html 2>/dev/null || xdg-open htmlcov/index.html 2>/dev/null || echo "Open htmlcov/index.html manually"

# ==============================================================================
# Code Quality
# ==============================================================================

lint: ## Run linter (ruff)
	@echo "$(BLUE)Running linter...$(NC)"
	@$(RUFF) check src/ tests/
	@echo "$(GREEN)✓ Linting passed$(NC)"

lint-fix: ## Run linter and auto-fix issues
	@echo "$(BLUE)Running linter with auto-fix...$(NC)"
	@$(RUFF) check --fix src/ tests/
	@echo "$(GREEN)✓ Linting complete$(NC)"

format: ## Format code with black and isort
	@echo "$(BLUE)Formatting code...$(NC)"
	@$(BLACK) src/ tests/
	@$(ISORT) src/ tests/
	@echo "$(GREEN)✓ Code formatted$(NC)"

format-check: ## Check if code is formatted correctly
	@echo "$(BLUE)Checking code formatting...$(NC)"
	@$(BLACK) --check src/ tests/
	@$(ISORT) --check-only src/ tests/
	@echo "$(GREEN)✓ Code is properly formatted$(NC)"

type-check: ## Run type checker (mypy)
	@echo "$(BLUE)Running type checker...$(NC)"
	@$(MYPY) src/
	@echo "$(GREEN)✓ Type checking passed$(NC)"

quality: format lint type-check ## Run all code quality checks
	@echo "$(GREEN)✓ All quality checks passed$(NC)"

# ==============================================================================
# Operations
# ==============================================================================

init-infra: ## Initialize infrastructure (create topics, tables, etc.)
	@echo "$(BLUE)Initializing infrastructure...$(NC)"
	@$(VENV)/bin/python scripts/init_tables.py
	@echo "$(GREEN)✓ Infrastructure initialized$(NC)"

simulate: docker-up ## Start market data simulation
	@echo "$(BLUE)Starting market data simulation...$(NC)"
	@$(VENV)/bin/python scripts/simulate_market_data.py

shell: ## Open Python shell with platform modules loaded
	@$(VENV)/bin/ipython -i -c "from k2 import *; print('K2 Platform modules loaded')"

kafka-topics: ## List Kafka topics
	@docker exec k2-kafka kafka-topics --bootstrap-server localhost:9092 --list

kafka-consume: ## Consume messages from market.ticks topic
	@docker exec -it k2-kafka kafka-console-consumer \
		--bootstrap-server localhost:9092 \
		--topic market.ticks \
		--from-beginning \
		--max-messages 10

minio-shell: ## Open MinIO client shell
	@docker exec -it k2-minio-init mc

# ==============================================================================
# Documentation
# ==============================================================================

docs: ## Build documentation
	@echo "$(BLUE)Building documentation...$(NC)"
	@cd docs && $(VENV)/bin/sphinx-build -b html . _build/html
	@echo "$(GREEN)✓ Documentation built in docs/_build/html$(NC)"

docs-serve: docs ## Build and serve documentation
	@echo "$(BLUE)Serving documentation at http://localhost:8001$(NC)"
	@cd docs/_build/html && python -m http.server 8001

# ==============================================================================
# CI/CD Helpers
# ==============================================================================

ci-test: ## Run tests as in CI (unit + integration)
	@make test-unit
	@make test-integration

ci-quality: ## Run quality checks as in CI
	@make format-check
	@make lint
	@make type-check

ci-all: ci-quality ci-test coverage ## Run all CI checks

# ==============================================================================
# Version Management
# ==============================================================================

version: ## Show current version
	@grep "version = " pyproject.toml | head -1 | cut -d'"' -f2

bump-patch: ## Bump patch version (0.1.0 -> 0.1.1)
	@echo "$(BLUE)Bumping patch version...$(NC)"
	@$(VENV)/bin/pip install bump2version
	@$(VENV)/bin/bump2version patch
	@echo "$(GREEN)✓ Version bumped$(NC)"

bump-minor: ## Bump minor version (0.1.0 -> 0.2.0)
	@echo "$(BLUE)Bumping minor version...$(NC)"
	@$(VENV)/bin/pip install bump2version
	@$(VENV)/bin/bump2version minor
	@echo "$(GREEN)✓ Version bumped$(NC)"

bump-major: ## Bump major version (0.1.0 -> 1.0.0)
	@echo "$(BLUE)Bumping major version...$(NC)"
	@$(VENV)/bin/pip install bump2version
	@$(VENV)/bin/bump2version major
	@echo "$(GREEN)✓ Version bumped$(NC)"

# ==============================================================================
# Utility Commands
# ==============================================================================

tree: ## Show project structure
	@tree -I '__pycache__|*.pyc|*.egg-info|.git|.venv|node_modules' -L 3

check-env: ## Check if environment is set up correctly
	@echo "$(BLUE)Checking environment...$(NC)"
	@which $(PYTHON) > /dev/null || (echo "$(RED)Python 3.11+ not found$(NC)" && exit 1)
	@which docker > /dev/null || (echo "$(RED)Docker not found$(NC)" && exit 1)
	@which docker-compose > /dev/null || (echo "$(RED)docker-compose not found$(NC)" && exit 1)
	@test -f .env || (echo "$(YELLOW)Warning: .env file not found. Copy from .env.example$(NC)")
	@echo "$(GREEN)✓ Environment check passed$(NC)"

install-deps-mac: ## Install system dependencies on macOS
	@echo "$(BLUE)Installing system dependencies...$(NC)"
	@brew install python@3.11 docker docker-compose tree
	@echo "$(GREEN)✓ Dependencies installed$(NC)"

install-deps-ubuntu: ## Install system dependencies on Ubuntu
	@echo "$(BLUE)Installing system dependencies...$(NC)"
	@sudo apt-get update
	@sudo apt-get install -y python3.11 python3.11-venv docker.io docker-compose tree
	@echo "$(GREEN)✓ Dependencies installed$(NC)"

# ==============================================================================
# Database Management
# ==============================================================================

db-shell: ## Open PostgreSQL shell
	@docker exec -it k2-postgres psql -U iceberg -d iceberg_catalog

db-migrate: ## Run database migrations
	@echo "$(BLUE)Running database migrations...$(NC)"
	@$(VENV)/bin/alembic upgrade head
	@echo "$(GREEN)✓ Migrations complete$(NC)"

db-rollback: ## Rollback last migration
	@echo "$(BLUE)Rolling back last migration...$(NC)"
	@$(VENV)/bin/alembic downgrade -1
	@echo "$(GREEN)✓ Rollback complete$(NC)"

db-reset: ## Drop and recreate database (WARNING: deletes all data)
	@echo "$(RED)WARNING: This will delete all database data!$(NC)"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		docker exec k2-postgres psql -U iceberg -c "DROP DATABASE IF EXISTS iceberg_catalog;"; \
		docker exec k2-postgres psql -U iceberg -c "CREATE DATABASE iceberg_catalog;"; \
		make db-migrate; \
		echo "$(GREEN)✓ Database reset complete$(NC)"; \
	else \
		echo "$(YELLOW)Aborted$(NC)"; \
	fi
