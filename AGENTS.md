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
- Always use the Makefile targets below (they bundle the right flags and tooling):
  - Install dev deps: `make install-dev`
  - Run locally: `make dev` (Python) or `make serve` (Uvicorn)
  - Format, lint, types: `make format && make lint && make type-check`
  - Unit tests (fast/coverage): `make test-fast` or `make test`
  - Docker integration tests: `make test-docker-quick` (fast) or `make test-docker`
  - Docker lifecycle helpers: `make test-docker-start`, `make test-docker-stop`, `make test-docker-clean`, `make test-docker-logs`
  - Docker run/build (manual): `make docker-run`, `make docker-build`

Notes
- Integration tests require Docker and Docker Compose (v1 or v2). The test harness auto-detects either `docker-compose` or `docker compose`.
- On CI, Docker tests are gated by a repo variable: set `RUN_DOCKER_TESTS=1` to enable the docker-tests job.

## Coding Style & Naming
- Python 3.11, 4‑space indent. Formatting: Black (88), isort (profile=black). Lint: flake8. Types: mypy.
- Names: `snake_case` (functions/vars), `PascalCase` (classes), `UPPER_SNAKE` (constants). Files: `snake_case.py`.
- Pre-commit: `make pre-commit` to install hooks.

## Testing Guidelines
- Framework: pytest (+ pytest‑asyncio, pytest‑cov). Markers: `unit`, `integration`, `load`, `slow`.
- Coverage: threshold configured via `pyproject.toml` (HTML at `htmlcov/`).
- Preferred commands:
  - Unit tests: `make test-fast` (iterate quickly) or `make test` (with coverage)
  - Docker integration: `make test-docker-quick` (subset) or `make test-docker` (full)
  - Health checks: `make test-health`; performance: `make test-performance`

Typical workflow
1) `make install-dev`
2) `make format && make lint && make type-check`
3) `make test-fast` (then `make test` before PR)
4) If your changes touch integration paths, run `make test-docker-quick`

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
- Format/lint/types: `make format && make lint && make type-check`
- Tests: `make test-fast` (or `make test` for coverage)
- Hooks: `make pre-commit` once, then `pre-commit run --all-files`

Changelog policy
- When a change is user‑visible (new feature, fix, docs, config, endpoints), update `CHANGELOG.md` under the `[Unreleased]` section before committing.
- Use Keep a Changelog format with "### Added/Changed/Fixed/Removed" headings.
- Summarize changes concisely; link to config keys/endpoints where helpful.

Housekeeping
- Generated qBittorrent runtime state (e.g., `test-data/qbit-config/.../qBittorrent-data.conf`) is ignored; tests recreate it as needed.

## Releases (GitHub CLI)

Use the GitHub CLI (`gh`) to update release notes after a tag/release exists.

- Prerequisites: `gh` installed and authenticated.
  - Check: `gh --version` and `gh auth status`
  - Login: `gh auth login` (GitHub.com → HTTPS → browser or token)

- Edit notes for an existing release (preferred):
  - Put notes in a file (e.g., `RELEASE_NOTES_0_3_5.md`).
  - Update release: `gh release edit v0.3.5 --title "v0.3.5" --notes-file RELEASE_NOTES_0_3_5.md`
  - Verify: `gh release view v0.3.5 --json tagName,url,body | jq -r '.tagName+"\n"+.url+"\n---\n"+.body'`

- If a release doesn’t exist for the tag yet:
  - Create: `gh release create v0.3.5 --title "v0.3.5" --notes-file RELEASE_NOTES_0_3_5.md`

- Tips:
  - Include a “Full Changelog” link: `https://github.com/<org>/<repo>/compare/vX.Y.Z-1...vX.Y.Z`
  - Add assets later: `gh release upload v0.3.5 dist/*`
  - In CI, set `GH_TOKEN`/`GITHUB_TOKEN` for non-interactive `gh` usage.
  - Cleanup: remove the temporary notes file after updating the release, e.g. `rm -f RELEASE_NOTES_0_3_5.md` (or your versioned notes filename).
