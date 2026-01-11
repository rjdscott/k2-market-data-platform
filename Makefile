.PHONY: help setup install dev-install clean clean-venv test test-unit test-integration \
        test-performance coverage lint format type-check quality docker-up \
        docker-down docker-logs docker-clean init-infra simulate docs \
        api api-prod api-test uv-lock uv-upgrade uv-add uv-add-dev \
        demo-reset demo-reset-force demo-reset-dry-run demo-reset-custom

# Default target
.DEFAULT_GOAL := help

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

# Python and environment (using uv)
VENV := .venv
UV := uv

# Tool execution via uv run (handles venv automatically)
PYTEST := $(UV) run pytest
BLACK := $(UV) run black
ISORT := $(UV) run isort
RUFF := $(UV) run ruff
MYPY := $(UV) run mypy

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
	@echo "$(BLUE)Creating virtual environment and installing dependencies...$(NC)"
	@$(UV) sync
	@echo "$(GREEN)✓ Installation complete$(NC)"

dev-install: ## Install with all development dependencies
	@echo "$(BLUE)Installing with development dependencies...$(NC)"
	@$(UV) sync --all-extras
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

clean-venv: ## Remove virtual environment (for fresh reinstall)
	@echo "$(BLUE)Removing virtual environment...$(NC)"
	@rm -rf $(VENV)
	@echo "$(GREEN)✓ Virtual environment removed. Run 'make install' to recreate.$(NC)"

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
# API Server
# ==============================================================================

api: ## Start API server in development mode
	@echo "$(BLUE)Starting K2 API server...$(NC)"
	@echo "$(YELLOW)API Docs: http://localhost:8000/docs$(NC)"
	@echo "$(YELLOW)Health:   http://localhost:8000/health$(NC)"
	@$(UV) run uvicorn k2.api.main:app --reload --host 0.0.0.0 --port 8000

api-prod: ## Start API server in production mode
	@echo "$(BLUE)Starting K2 API server (production)...$(NC)"
	@$(UV) run gunicorn k2.api.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000

api-test: ## Test API endpoints with curl
	@echo "$(BLUE)Testing API endpoints...$(NC)"
	@echo ""
	@echo "$(YELLOW)Health check:$(NC)"
	@curl -s http://localhost:8000/health | python -m json.tool || echo "$(RED)API not running$(NC)"
	@echo ""
	@echo "$(YELLOW)Trades (with auth):$(NC)"
	@curl -s -H "X-API-Key: k2-dev-api-key-2026" "http://localhost:8000/v1/trades?limit=3" | python -m json.tool || echo "$(RED)Request failed$(NC)"
	@echo ""
	@echo "$(GREEN)✓ API test complete$(NC)"

# ==============================================================================
# Operations
# ==============================================================================

init-infra: ## Initialize infrastructure (create topics, tables, etc.)
	@echo "$(BLUE)Initializing infrastructure...$(NC)"
	@$(UV) run python scripts/init_infra.py
	@echo "$(GREEN)✓ Infrastructure initialized$(NC)"

simulate: docker-up ## Start market data simulation
	@echo "$(BLUE)Starting market data simulation...$(NC)"
	@$(UV) run python scripts/simulate_market_data.py

shell: ## Open Python shell with platform modules loaded
	@$(UV) run ipython -i -c "from k2 import *; print('K2 Platform modules loaded')"

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
# Implementation Plan Management
# ==============================================================================

plan-status: ## Show current implementation progress
	@echo "$(BLUE)K2 Implementation Progress$(NC)"
	@echo ""
	@grep -A 10 "^## Current Status" docs/phases/phase-1-portfolio-demo/PROGRESS.md | head -15

plan-steps: ## List all implementation steps with status
	@echo "$(BLUE)Implementation Steps Status:$(NC)"
	@echo ""
	@for file in docs/phases/phase-1-portfolio-demo/steps/step-*.md; do \
		step=$$(basename $$file .md | sed 's/step-//'); \
		status=$$(grep "^\*\*Status\*\*:" $$file | sed 's/.*: //; s/^ *//'); \
		title=$$(head -1 $$file | sed 's/^# //'); \
		printf "  %-50s %s\n" "$$title" "$$status"; \
	done

plan-check: ## Check how many steps are completed
	@echo "$(BLUE)Checking implementation progress...$(NC)"
	@completed=$$(grep -l "Status\*\*: ✅" docs/phases/phase-1-portfolio-demo/steps/*.md | wc -l | tr -d ' '); \
	total=$$(ls docs/phases/phase-1-portfolio-demo/steps/step-*.md | wc -l | tr -d ' '); \
	percentage=$$((completed * 100 / total)); \
	echo "$(GREEN)Steps completed: $$completed/$$total ($$percentage%)$(NC)"

plan-todo: ## Show next steps to work on
	@echo "$(BLUE)Next Steps:$(NC)"
	@echo ""
	@grep -A 3 "Next Up:" docs/phases/phase-1-portfolio-demo/PROGRESS.md | head -5

plan-open: ## Open implementation plan in browser
	@echo "$(BLUE)Opening implementation plan...$(NC)"
	@open docs/phases/phase-1-portfolio-demo/IMPLEMENTATION_PLAN.md 2>/dev/null || \
	 xdg-open docs/phases/phase-1-portfolio-demo/IMPLEMENTATION_PLAN.md 2>/dev/null || \
	 echo "$(YELLOW)Open docs/phases/phase-1-portfolio-demo/IMPLEMENTATION_PLAN.md manually$(NC)"

plan-verify: ## Run verification checklist
	@echo "$(BLUE)Implementation Verification Checklist$(NC)"
	@echo ""
	@cat docs/phases/phase-1-portfolio-demo/reference/verification-checklist.md | \
	 grep -E "^\- \[" | head -20
	@echo ""
	@echo "$(YELLOW)See docs/phases/phase-1-portfolio-demo/reference/verification-checklist.md for full checklist$(NC)"

# ==============================================================================
# Documentation
# ==============================================================================

docs: ## Build documentation
	@echo "$(BLUE)Building documentation...$(NC)"
	@cd docs && $(UV) run sphinx-build -b html . _build/html
	@echo "$(GREEN)✓ Documentation built in docs/_build/html$(NC)"

docs-serve: docs ## Build and serve documentation
	@echo "$(BLUE)Serving documentation at http://localhost:8001$(NC)"
	@cd docs/_build/html && python -m http.server 8001

# ==============================================================================
# Demo & E2E Testing
# ==============================================================================

demo: ## Run interactive platform demo
	@echo "$(BLUE)Starting K2 Platform Demo...$(NC)"
	@$(UV) run python scripts/demo.py

demo-quick: ## Run demo without delays (CI mode)
	@echo "$(BLUE)Starting K2 Platform Demo (quick mode)...$(NC)"
	@$(UV) run python scripts/demo.py --quick

test-e2e: docker-up ## Run E2E integration tests
	@echo "$(BLUE)Running E2E integration tests...$(NC)"
	@sleep 5  # Wait for services to be ready
	@$(PYTEST) tests/integration/test_e2e_flow.py -v -s -m integration
	@echo "$(GREEN)✓ E2E tests passed$(NC)"

notebook: ## Start Jupyter notebook server
	@echo "$(BLUE)Starting Jupyter notebook server...$(NC)"
	@echo "$(YELLOW)Notebooks: http://localhost:8888$(NC)"
	@SSL_CERT_FILE=$$($(UV) run python -c "import certifi; print(certifi.where())") \
		$(UV) run jupyter notebook notebooks/

notebook-install: ## Install notebook dependencies
	@echo "$(BLUE)Installing notebook dependencies...$(NC)"
	@$(UV) add jupyter matplotlib mplfinance requests
	@echo "$(GREEN)✓ Notebook dependencies installed$(NC)"

# ==============================================================================
# Demo Reset
# ==============================================================================

demo-reset: ## Reset demo environment (full reset with confirmation)
	@echo "$(BLUE)Starting demo reset...$(NC)"
	@$(UV) run python scripts/reset_demo.py

demo-reset-force: ## Reset demo environment (skip confirmation)
	@echo "$(BLUE)Force resetting demo environment...$(NC)"
	@$(UV) run python scripts/reset_demo.py --force

demo-reset-dry-run: ## Preview demo reset operations
	@echo "$(BLUE)Preview demo reset (dry run)...$(NC)"
	@$(UV) run python scripts/reset_demo.py --dry-run

demo-reset-custom: ## Reset with flags (KEEP_METRICS=1, KEEP_KAFKA=1, KEEP_ICEBERG=1, NO_RELOAD=1, FORCE=1)
	@echo "$(BLUE)Custom demo reset...$(NC)"
	@$(UV) run python scripts/reset_demo.py \
		$(if $(KEEP_METRICS),--keep-metrics,) \
		$(if $(KEEP_KAFKA),--keep-kafka,) \
		$(if $(KEEP_ICEBERG),--keep-iceberg,) \
		$(if $(NO_RELOAD),--no-reload,) \
		$(if $(FORCE),--force,)

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
	@$(UV) add --dev bump2version
	@$(UV) run bump2version patch
	@echo "$(GREEN)✓ Version bumped$(NC)"

bump-minor: ## Bump minor version (0.1.0 -> 0.2.0)
	@echo "$(BLUE)Bumping minor version...$(NC)"
	@$(UV) add --dev bump2version
	@$(UV) run bump2version minor
	@echo "$(GREEN)✓ Version bumped$(NC)"

bump-major: ## Bump major version (0.1.0 -> 1.0.0)
	@echo "$(BLUE)Bumping major version...$(NC)"
	@$(UV) add --dev bump2version
	@$(UV) run bump2version major
	@echo "$(GREEN)✓ Version bumped$(NC)"

# ==============================================================================
# Utility Commands
# ==============================================================================

tree: ## Show project structure
	@tree -I '__pycache__|*.pyc|*.egg-info|.git|.venv|node_modules' -L 3

check-env: ## Check if environment is set up correctly
	@echo "$(BLUE)Checking environment...$(NC)"
	@which uv > /dev/null || (echo "$(RED)uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh$(NC)" && exit 1)
	@which docker > /dev/null || (echo "$(RED)Docker not found$(NC)" && exit 1)
	@which docker-compose > /dev/null || (echo "$(RED)docker-compose not found$(NC)" && exit 1)
	@test -f .env || (echo "$(YELLOW)Warning: .env file not found. Copy from .env.example$(NC)")
	@echo "$(GREEN)✓ Environment check passed$(NC)"

install-deps-mac: ## Install system dependencies on macOS
	@echo "$(BLUE)Installing system dependencies...$(NC)"
	@brew install python@3.13 docker docker-compose tree
	@echo "$(GREEN)✓ Dependencies installed$(NC)"

install-deps-ubuntu: ## Install system dependencies on Ubuntu
	@echo "$(BLUE)Installing system dependencies...$(NC)"
	@sudo apt-get update
	@sudo apt-get install -y python3.13 python3.13-venv docker.io docker-compose tree
	@echo "$(GREEN)✓ Dependencies installed$(NC)"

# ==============================================================================
# Database Management
# ==============================================================================

db-shell: ## Open PostgreSQL shell
	@docker exec -it k2-postgres psql -U iceberg -d iceberg_catalog

db-migrate: ## Run database migrations
	@echo "$(BLUE)Running database migrations...$(NC)"
	@$(UV) run alembic upgrade head
	@echo "$(GREEN)✓ Migrations complete$(NC)"

db-rollback: ## Rollback last migration
	@echo "$(BLUE)Rolling back last migration...$(NC)"
	@$(UV) run alembic downgrade -1
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

# ==============================================================================
# UV Package Management
# ==============================================================================

uv-lock: ## Update lock file after pyproject.toml changes
	@echo "$(BLUE)Updating uv.lock...$(NC)"
	@$(UV) lock
	@echo "$(GREEN)✓ Lock file updated$(NC)"

uv-upgrade: ## Upgrade all dependencies to latest compatible versions
	@echo "$(BLUE)Upgrading dependencies...$(NC)"
	@$(UV) lock --upgrade
	@$(UV) sync
	@echo "$(GREEN)✓ Dependencies upgraded$(NC)"

uv-add: ## Add a new dependency (usage: make uv-add PKG=package-name)
	@echo "$(BLUE)Adding $(PKG)...$(NC)"
	@$(UV) add $(PKG)
	@echo "$(GREEN)✓ $(PKG) added$(NC)"

uv-add-dev: ## Add a new dev dependency (usage: make uv-add-dev PKG=package-name)
	@echo "$(BLUE)Adding $(PKG) as dev dependency...$(NC)"
	@$(UV) add --dev $(PKG)
	@echo "$(GREEN)✓ $(PKG) added to dev dependencies$(NC)"
