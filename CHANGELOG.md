% Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project aims to follow Semantic Versioning.

## [Unreleased]
- Additions and improvements under development.

## [0.1.0] - 2025-09-02
### Added
- Phase 1 per‑tracker upload limits with equal distribution.
- Webhook handler for qBittorrent events (<10ms response).
- Allocation engine with gradual rollout and differential updates.
- SQLite‑backed rollback system with full restore endpoint.
- Docker deployment and Docker‑based integration tests.
- Unit, integration, and load tests; CI for lint, type‑check, tests, and quick Docker tests.
- Multi‑arch Docker images (linux/amd64, linux/arm64) via GitHub Actions.

### Changed
- Normalize /rollout and /rollback to return 400 on bad requests.
- Replace Pydantic `dict()` with `model_dump()` to remove deprecations.

### Removed
- Legacy integration scripts and targets replaced by unified Docker test runner.

[Unreleased]: https://github.com/OWNER/REPO/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/REPO/releases/tag/v0.1.0
