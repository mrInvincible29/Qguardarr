% Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project aims to follow Semantic Versioning.

## [Unreleased]

## [0.3.6] - 2025-09-05
### Added
- feat(config): `global.auto_unlimit_on_inactive` (default false). When enabled, Qguardarr sets per-torrent upload limits to unlimited (-1) as soon as a torrent is not active in the current cycle.

### Changed
- feat(allocation): Apply auto-unlimit for inactive torrents after differential updates; records rollback entries with reason `auto_unlimit_inactive`. Dry-run persists -1 in the dry-run store without calling qBittorrent.

### Tests
- test: Add unit tests for auto-unlimit behavior in real and dry-run modes.

## [0.3.5] - 2025-09-04
### Added
- feat(config): `global.cache_ttl_seconds` to control TorrentCache cleanup TTL (default 1800s).

### Changed
- perf(allocation): Only include active torrents; remove cache backfill-by-hashes API calls.
- perf(allocation): Use configured TTL for cache cleanup to avoid stale entries.
- chore(qbit): Add DEBUG logs for tracker URL selection and fallbacks.

### Documentation
- docs(readme): Simplify Docker Quick Start, clarify config essentials, and document cache TTL.

### Tests
- test: Update active-torrents unit to assert no backfill; add TTL cleanup test.

## [0.3.4] - 2025-09-04
### Changed
- chore(config): Default `global.rollout_percentage` is now 100 when omitted (previous default was 10). If you don’t specify it in config, Qguardarr manages 100% of eligible torrents.
- chore(matching): Normalize shorthand tracker patterns like `.example\\.com.` into `.*example\\.com.*` unless anchored with `^`/`$`, making config patterns more forgiving.

### Added
- feat(logging): DEBUG logs now include the selected tracker URL per torrent and the matched tracker with current upload speed (e.g., `up=2.95 MiB/s`).
- feat(api): `/match/test?url=...&detailed=true` endpoint to test a tracker URL against configured patterns.

### Tests
- test: Add unit tests for pattern normalization and the new match-test endpoint.

## [0.3.3] - 2025-09-04
### Added
- feat(api): `GET /stats/managed` returns managed torrents grouped by tracker with `hash`, `current_limit`, `added_at`, `last_seen`, and `age_seconds`.

### Fixed
- fix(stats): For unlimited trackers (configured `max_upload_speed <= 0`), report `configured_limit_mbps: null` instead of `-0`.

### Notes
- Logs now go to the file configured in `logging.file`; Uvicorn writes console output. Use `tail -f logs/qguardarr.log` to watch allocation cycles.

## [0.3.2] - 2025-09-04
### Changed
- perf(qbit): Eliminate full-list queries. Use `filter=active` + upspeed threshold and backfill only a bounded subset of cached hashes via `GET /torrents/info?hashes=...` (cap 1000). Trackers are fetched only for the filtered subsets.
- chore(tests): Update unit tests to validate backfill-by-hashes behavior.
- chore(runner): Quick Docker test runner now waits for Qguardarr and executes the config hot-reload test in quick mode.

### Added
- feat(config): Automatic config hot-reload via background watcher (mtime-based). Manual `POST /config/reload` endpoint for ops.
- test(integration): Hot-reload integration test that edits host `config/qguardarr.yaml` and asserts rollout change.

### Security
- No changes since 0.3.1; single-password auth and log redaction remain enforced.


## [0.3.1] - 2025-09-03
### Changed
- perf(qbit): Query torrents with `filter=active` (instead of `uploading`) and only fetch trackers for torrents with `upspeed >= active_torrent_threshold_kb*1024`. This significantly reduces qBittorrent API calls on large libraries.

### Tests
- test: Add unit test to verify `filter=active` is used and tracker lookups are performed only for the active subset.

### Documentation
- docs(config): Example config now reads `global.port` from `APP_PORT` to align with Compose.

## [0.3.0] - 2025-09-03
### Added
- feat(phase3): Soft per‑tracker borrowing strategy (`global.allocation_strategy: soft`) with priority‑weighted pooling and smoothing (EMA + min delta).
- feat(api): `GET /preview/next-cycle` to preview proposed per‑torrent limits and per‑tracker effective caps (includes humanized summary fields).
- feat(api): `POST /smoothing/reset` to clear soft smoothing state (per‑tracker or all).
- feat(api): `POST /limits/reset` to set upload limits to unlimited for torrents touched by Qguardarr (dry‑run supported).
- feat(stats): Expose current allocation `strategy` in `/stats`; enhance `/stats/trackers` with `effective_cap_mbps`, `borrowed_mbps`.

### Security
- fix(security): Remove multi‑password auth fallback list; authenticate only with configured credentials. Redact passwords from logs.

### Changed
- chore(docker): Default `docker-compose.yml` now pulls `ghcr.io/mrinvincible29/qguardarr:latest` instead of building locally.
- chore(docker): Remove `version:` key from Compose files to avoid warnings; publish `APP_PORT` env for port binding/health.
- chore(docker): Drop `mem_limit` from Compose; rely on host scheduling or user‑provided limits.
- chore(logging): Centralize logging setup; ensure log directory exists; avoid file handler permission errors.

### Documentation
- docs: Docker Quick Start updated to run directly from GHCR (no git clone needed) with repo‑sourced compose and config; add `.env` with `APP_PORT`.
- docs: Clarify that qBittorrent webhooks are optional (periodic cycles still work) and explain benefits when enabled.

### Tests
- test: Add unit test to enforce single‑password auth and log redaction; run Docker quick integration tests.

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

[Unreleased]: https://github.com/mrInvincible29/Qguardarr/compare/v0.3.6...HEAD
[0.3.6]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.3.6
[0.3.5]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.3.5
[0.3.4]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.3.4
[0.3.3]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.3.3
[0.3.2]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.3.2
[0.3.1]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.3.1
[0.3.0]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.3.0
[0.2.0]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.2.0
[0.1.0]: https://github.com/mrInvincible29/Qguardarr/releases/tag/v0.1.0
