% Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project aims to follow Semantic Versioning.

## [Unreleased]
- Additions and improvements under development.

## [0.2.0] - 2025-09-02
### Added
- feat(allocation): Optional Phase 2 weighted within‑tracker allocator with smart torrent scoring (ActivityScorer) and active‑set selection.
- feat(config): New `global.allocation_strategy` (`equal`|`weighted`, default `equal`) and `global.max_managed_torrents` (default `1000`).
- docs(config): Example config and README updated with Phase 2 usage and knobs.
- docs(api): Document `/config` response includes the new Phase 2 fields (passwords/API keys still masked), and `/stats` fields (`managed_torrent_count`, `score_distribution`).

### Migration notes
- No breaking changes. Existing configs continue using Phase 1 equal split by default.
- To enable Phase 2 behavior, add under `global` in `config/qguardarr.yaml`:
  ```yaml
  allocation_strategy: weighted
  max_managed_torrents: 1000  # tune as needed
  ```

## [0.1.0] - 2025-09-02
### Added
- Phase 1 per‑tracker upload limits with equal distribution.
- Webhook handler for qBittorrent events (<10ms response).
- Allocation engine with gradual rollout and differential updates.
- SQLite‑backed rollback system with full restore endpoint.
- Docker deployment and Docker‑based integration tests.
- Unit, integration, and load tests; CI for lint, type‑check, tests, and quick Docker tests.
- Multi‑arch Docker images (linux/amd64, linux/arm64) via GitHub Actions.
- Support for unlimited per-tracker caps by setting `max_upload_speed: -1` in tracker config.
- Allocation engine sets per-torrent upload to `-1` for trackers configured as unlimited.
- Unit tests for catch-all unlimited behavior, switching a tracker from finite to unlimited (removes caps), specific-over-default precedence, and overlapping pattern order.

### Changed
- Normalize /rollout and /rollback to return 400 on bad requests.
- Replace Pydantic `dict()` with `model_dump()` to remove deprecations.

### Documentation
- README: Document unlimited (`-1`) semantics, matching precedence, and multi-tracker handling.
- Config examples: Note that `-1` means unlimited/no cap for trackers (including catch-all).

### Removed
- Legacy integration scripts and targets replaced by unified Docker test runner.

[Unreleased]: https://github.com/mrInvincible29/Qguardarr/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.2.0
[0.1.0]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.1.0
