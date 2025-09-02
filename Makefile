# Makefile for Qguardarr development

.PHONY: help install install-dev test lint format type-check clean docker-build docker-run pre-commit

# Default target
help:
	@echo "Available commands:"
	@echo ""
	@echo "📦 Installation:"
	@echo "  install             - Install production dependencies"
	@echo "  install-dev         - Install development dependencies"
	@echo ""
	@echo "🧪 Testing:"
	@echo "  test                - Run unit tests with coverage"
	@echo "  test-fast           - Run unit tests without coverage (faster)"
	@echo "  test-integration    - Run integration tests (requires services)"
	@echo "  test-load           - Run load/performance tests"
	@echo "  test-all            - Run all unit tests"
	@echo ""
	@echo "🐳 Docker Testing:"
	@echo "  test-docker              - Run Docker integration tests (auto setup/teardown)"
	@echo "  test-docker-quick        - Run quick Docker tests only"
	@echo "  test-docker-verbose      - Run Docker tests with verbose output"
	@echo "  test-docker-clean        - Force cleanup of Docker containers"
	@echo "  test-docker-setup        - Manual Docker test environment setup"
	@echo "  test-docker-start        - Start Docker test services manually"
	@echo "  test-docker-stop         - Stop Docker test services manually" 
	@echo "  test-docker-logs         - Show Docker service logs"
	@echo ""
	@echo "🔍 Health & Performance:"
	@echo "  test-health         - Check service health"
	@echo "  test-performance    - Show system performance"
	@echo ""
	@echo "🔧 Code Quality:"
	@echo "  lint         - Run linting (flake8)"
	@echo "  format       - Format code (black + isort)"
	@echo "  type-check   - Run type checking (mypy)"
	@echo "  quality      - Run all code quality checks"
	@echo ""
	@echo "🚀 Deployment:"
	@echo "  docker-build - Build Docker image"
	@echo "  docker-run   - Run with Docker Compose"
	@echo "  clean        - Clean up build artifacts"
	@echo "  pre-commit   - Install pre-commit hooks"

# Installation
install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

# Testing
test:
	@echo "Running unit tests with coverage..."
	pytest tests/unit/ --cov=src --cov-report=term-missing --cov-report=html --cov-fail-under=15 -v

test-fast:
	@echo "Running unit tests (fast)..."
	pytest tests/unit/ -x -v

test-watch:
	pytest-watch

test-integration:
	@echo "Running integration tests..."
	@echo "Note: Requires qBittorrent and Qguardarr services running"
	pytest tests/integration/ -v -m integration

test-load:
	@echo "Running load tests..."
	@echo "Note: Set RUN_LOAD_TESTS=true to enable load tests"
	RUN_LOAD_TESTS=true pytest tests/load/ -v -m load

test-all:
	@echo "Running all unit tests..."
	pytest tests/unit/ -v
	@echo ""
	@echo "Integration and load tests require services - run separately:"
	@echo "  make test-integration"
	@echo "  make test-load"

# Docker-based integration testing
test-docker-setup:
	@echo "Setting up Docker-based test environment..."
	docker-compose -f docker-compose.test.yml down --volumes --remove-orphans || true
	docker system prune -f --volumes || true
	mkdir -p test-data/{qbit-config,downloads,torrents,qguardarr-data,qguardarr-logs}
	
test-docker-start:
	@echo "Starting Docker test services..."
	cp config/qguardarr.test.yaml config/qguardarr.yaml
	docker-compose -f docker-compose.test.yml up -d --build

test-docker-stop:
	@echo "Stopping Docker test services..."
	docker-compose -f docker-compose.test.yml down --volumes --remove-orphans

test-docker-logs:
	@echo "Showing Docker service logs..."
	docker-compose -f docker-compose.test.yml logs --tail=50

# New improved Docker testing targets
test-docker:
	@echo "🐳 Running Docker integration tests (automatic setup/teardown)..."
	@bash scripts/test-docker-integration.sh

test-docker-quick:
	@echo "🐳 Running quick Docker tests..."
	@bash scripts/test-docker-integration.sh --quick

test-docker-verbose:
	@echo "🐳 Running Docker tests with verbose output..."
	@bash scripts/test-docker-integration.sh --verbose

test-docker-clean:
	@echo "🧹 Force cleanup of Docker test containers..."
	@bash scripts/test-docker-integration.sh --cleanup

# Legacy integration test removed; use `make test-docker`

# Performance and health checks
test-performance:
	@echo "Running performance tests..."
	@echo "System info:"
	@python -c "import psutil; print(f'Memory: {psutil.virtual_memory().percent:.1f}% used')"
	@python -c "import psutil; print(f'CPU: {psutil.cpu_percent(interval=1):.1f}% used')"

test-health:
	@echo "Checking service health..."
	@curl -f http://localhost:8089/health 2>/dev/null | python -m json.tool || echo "❌ Qguardarr not running"
	@curl -f http://localhost:8080 >/dev/null 2>&1 && echo "✅ qBittorrent: OK" || echo "❌ qBittorrent: Not running"

# Code quality
lint:
	flake8 src/ tests/

format:
	black src/ tests/
	isort src/ tests/

type-check:
	mypy src/

# Run all quality checks
quality: format lint type-check test-fast

# Docker
docker-build:
	docker-compose build

docker-run:
	docker-compose up --build

docker-logs:
	docker-compose logs -f

# Development setup
pre-commit:
	pre-commit install
	pre-commit run --all-files

# Cleanup
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build/
	rm -rf dist/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/

# Database utilities
reset-db:
	rm -f data/rollback.db
	echo "Database reset"

# Development server
dev:
	python -m src.main

# Production-like server
serve:
	uvicorn src.main:app --host 0.0.0.0 --port 8089 --reload

# Generate requirements.txt from pyproject.toml (if using pip-tools)
requirements:
	pip-compile --output-file requirements.txt pyproject.toml
	pip-compile --extra dev --output-file requirements-dev.txt pyproject.toml

# Show project info
info:
	@echo "Project: Qguardarr"
	@echo "Python: $(shell python --version)"
	@echo "Pip packages: $(shell pip list | wc -l) installed"
	@echo "Tests: $(shell find tests -name '*.py' | wc -l) files"
	@echo "Source: $(shell find src -name '*.py' | wc -l) files"
