.PHONY: help up down logs ps test test-kotlin test-python
.DEFAULT_GOAL := help

help:  ## Show available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

up:  ## Start the full stack (builds images on first run)
	docker compose up -d

down:  ## Stop the stack (volumes kept)
	docker compose down

logs:  ## Tail all service logs
	docker compose logs -f

ps:  ## Show service status
	docker compose ps

test: test-kotlin test-python  ## Run all unit tests

test-kotlin:  ## Feed handler unit tests (needs JDK 21)
	cd services/feed-handler-kotlin && ./gradlew test --no-daemon

test-python:  ## Iceberg offload flow unit tests (needs uv)
	uv run --no-project --with prefect --with pytest pytest tests -q
