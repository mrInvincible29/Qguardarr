# Repository Guidelines

This guide helps contributors work efficiently on Qguardarr. It reflects Phase 1 (hard per‑tracker limits) and current tooling.

## Project Structure & Modules
- `src/`: Core service
  - `main.py` (FastAPI app, endpoints), `allocation.py` (Phase 1 hard‑limit engine + `TorrentCache`), `qbit_client.py` (qBittorrent API with circuit breaker/batching), `webhook_handler.py` (queue‑only handler + cross‑seed forwarder), `tracker_matcher.py` (regex tracker matching), `rollback.py` (SQLite rollback).
- `tests/`: `unit/`, `integration/`, `load/`, `tests/test-data/` fixtures.
- `config/`: `qguardarr.yaml` (runtime), examples for local/test.
- `scripts/`: Docker/integration helpers (`test-docker-integration.sh`, `start.sh`).
- `logs/`, `data/`: runtime output and rollback DB.
- `Makefile`, `docker-compose*.yml`, `Dockerfile`.

## Build, Test, and Development
- Install dev deps: `make install-dev`
- Run locally: `make dev` (Python) or `make serve` (Uvicorn).
- Unit tests: `make test` (coverage) or `make test-fast`.
- Integration (Docker): `make test-docker` or `make test-docker-quick`.
- Docker run: `make docker-run`; build: `make docker-build`.

## Coding Style & Naming
- Python 3.11, 4‑space indent. Formatting: Black (88), isort (profile=black). Lint: flake8. Types: mypy.
- Names: `snake_case` (functions/vars), `PascalCase` (classes), `UPPER_SNAKE` (constants). Files: `snake_case.py`.
- Pre-commit: `make pre-commit` to install hooks.

## Testing Guidelines
- Framework: pytest (+ pytest‑asyncio, pytest‑cov). Markers: `unit`, `integration`, `load`, `slow`.
- Coverage: threshold configured via `pyproject.toml` (HTML at `htmlcov/`).
- Quick examples: `pytest tests/unit -v`; Docker integration: `make test-docker-quick`.

## Architecture (Phase 1)
- Single FastAPI service (port 8089) with components:
  - Webhook Handler: parses and enqueues events (<10ms) and optionally forwards completes to cross‑seed.
  - Allocation Engine: periodic cycles over active torrents, hard per‑tracker caps, differential updates, gradual rollout.
  - `TorrentCache`: O(1) lookups; tracks only active/recent torrents.
  - qBittorrent Client: auth, rate limiting, batching, circuit breaker.
  - Rollback Manager: records changes in SQLite; `/rollback` restores.
- Key endpoints: `/health`, `/stats`, `/stats/trackers`, `POST /webhook`, `POST /cycle/force`, `POST /rollback`, `POST /rollout`.

## Commit & PR Guidelines
- Use Conventional Commits (e.g., `feat:`, `fix:`, `docs:`, `test:`, `chore:`; optional `scope`). Imperative, concise subject; details in body.
- PRs: clear description, linked issues, config diffs if applicable, logs/screenshots of `/stats` for behavior changes, and tests for new logic.

## Before You Commit (required)
- Format and lint: `make format && make lint && make type-check`
- Run tests: `make test-fast` (or `make test` for coverage)
- Ensure hooks: `pre-commit run --all-files`
