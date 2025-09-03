# Qguardarr - qBittorrent Per-Tracker Speed Limiter

A production-ready system to dynamically limit upload speeds on a per-tracker basis for qBittorrent.

## Features

- **Collective per-tracker limits**: Sum of all torrents for a tracker must not exceed configured limit
- **Real-time response**: New torrents get limits within 1 minute via webhook events
- **Complete rollback capability**: Restore qBittorrent to original state on demand
- **Gradual rollout**: Test on subset of torrents before full deployment
- **Memory efficient**: <60MB RAM for managing thousands of torrents
- **Hot-reload configuration**: Change settings without service restart

## Phase 1 Implementation Status ✅

This is the **Phase 1 MVP** implementation featuring:

✅ **Basic per-tracker limits (hard limits)** - Each tracker gets a fixed upload speed limit  
✅ **Active torrent tracking only** - Monitors ~500-3000 actively uploading torrents instead of all 30K+  
✅ **Fast webhook handling** - <10ms response time to prevent qBittorrent timeouts  
✅ **SQLite rollback system** - Track and reverse all limit changes  
✅ **Equal distribution** - Fair bandwidth sharing within each tracker  
✅ **Gradual rollout** - Start with 10% of torrents, increase safely  
✅ **Docker deployment** - One-command setup with docker-compose  


## Quick Start

### Option 1: Docker (Recommended)

Run directly from the published GHCR image — no git clone required.

1. **Create a working folder**:
   ```bash
   mkdir -p qguardarr/{config,data,logs}
   cd qguardarr
   ```

2. **Fetch Compose file from repo** (no inline copy):
   ```bash
   # docker-compose.yml
   curl -sSLO https://raw.githubusercontent.com/mrInvincible29/Qguardarr/main/docker-compose.yml
   # Optional Mac/Windows overrides
   curl -sSLO https://raw.githubusercontent.com/mrInvincible29/Qguardarr/main/docker-compose.override.yml
   ```

3. **Create environment file** (or download example and edit):
   ```bash
   # Quick create
   cat > .env << 'EOF'
   QBIT_HOST=host.docker.internal
   QBIT_PORT=8080
   QBIT_USERNAME=admin
   QBIT_PASSWORD=your_password_here
   CROSS_SEED_URL=http://host.docker.internal:2468/api/webhook
   CROSS_SEED_API_KEY=
   EOF
   # Or: curl -sSLo .env https://raw.githubusercontent.com/mrInvincible29/Qguardarr/main/.env.example
   ```

4. **Get config from repo and edit**:
   ```bash
   curl -sSLo config/qguardarr.yaml \
     https://raw.githubusercontent.com/mrInvincible29/Qguardarr/main/config/qguardarr.yaml.example
   # Then open config/qguardarr.yaml and customize trackers and limits
   ```

5. **Start the service**:
   ```bash
   docker compose up -d
   # or: docker-compose up -d
   ```

6. **Optional: Configure qBittorrent webhook (faster response)**:
   In qBittorrent → Options → Downloads → "Run external program on torrent completion":
   ```bash
   curl -XPOST http://localhost:8089/webhook \
     --data-urlencode "event=complete" \
     --data-urlencode "hash=%I" \
     --data-urlencode "name=%N" \
     --data-urlencode "tracker=%T"
   ```

### Notes

- Ports: The container listens on `APP_PORT` (default `8089`). If you change `global.port` in `config/qguardarr.yaml`, also set `APP_PORT` in `.env` to the same value so the container port and health check match. Example: `APP_PORT=8189` and update config `global.port: 8189`.
- Linux permissions: Ensure `data/` and `logs/` are writable by your user before starting so the container can write through the bind mounts.
  - Fresh setup: `mkdir -p data logs`
  - If Docker already created them as root: `sudo chown -R $(id -u):$(id -g) data logs`
- Compose version warning: Compose V2 ignores the `version:` key; the repo compose omits it to avoid warnings.

## Webhooks (Optional, Recommended)

- Purpose: Webhooks let qBittorrent notify Qguardarr when torrents are added, completed, or deleted. Qguardarr queues these events immediately and prioritizes affected torrents/trackers in the next allocation cycle. It also supports forwarding completion events to cross-seed.
- Not required: Qguardarr runs periodic allocation cycles at `global.update_interval` even without webhooks. New/changed torrents will be processed on the next cycle.
- Benefits when enabled:
  - Faster reaction to new/completed torrents (prioritized next cycle instead of waiting blindly).
  - More targeted work: when the webhook includes a tracker URL, Qguardarr schedules an update for that tracker specifically, reducing unnecessary API churn.
  - Optional cross-seed forwarding with retry.
- Tradeoffs if disabled:
  - Updates apply only on the next scheduled cycle; worst-case delay ≈ `update_interval` (default 300s).
  - You can lower `update_interval` to reduce latency, but that increases qBittorrent API calls per hour.
- Recommendation: Keep a moderate `update_interval` (e.g., 300s) and enable webhooks for timely, efficient updates.

Webhook setup examples (qBittorrent → Options → Downloads → External program):
- On torrent completion:
  ```bash
  curl -XPOST http://localhost:8089/webhook \
    --data-urlencode "event=complete" \
    --data-urlencode "hash=%I" \
    --data-urlencode "name=%N" \
    --data-urlencode "tracker=%T" \
    --data-urlencode "category=%L" \
    --data-urlencode "tags=%G" \
    --data-urlencode "save_path=%D"
  ```
- On torrent added (if using an "on add" hook or script integration):
  ```bash
  curl -XPOST http://localhost:8089/webhook \
    --data-urlencode "event=add" \
    --data-urlencode "hash=%I" \
    --data-urlencode "name=%N" \
    --data-urlencode "tracker=%T"
  ```

Security tip: If exposing Qguardarr outside localhost, secure access (network ACLs or reverse proxy with auth). The `/webhook` endpoint is designed to be low-cost and resilient; it always responds quickly (<10ms) and processes events asynchronously.

### Option 2: Direct Python

1. **Setup environment**:
   ```bash
   git clone <repository>
   cd qguardarr
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure and run**:
   ```bash
   cp config/qguardarr.yaml.example config/qguardarr.yaml
   # Edit qguardarr.yaml with your settings
   export QBIT_PASSWORD="your_password"
   python -m src.main
   ```

### Option 3: Startup Script

```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

## Configuration

### Important Settings

**Safety Settings (start here)**:
```yaml
global:
  # Safety & performance
  rollout_percentage: 10           # Start with 10% of torrents
  update_interval: 300             # Check every 5 minutes
  differential_threshold: 0.2      # Only update >20% changes
  max_api_calls_per_cycle: 500

  # Strategy:
  # - equal (Phase 1)
  # - weighted (Phase 2)
  # - soft (Phase 3)
  allocation_strategy: equal
  max_managed_torrents: 1000       # cap the actively managed set

  # Phase 3 soft limits (used when allocation_strategy: soft)
  borrow_threshold_ratio: 0.9      # qualify for borrowing when usage >= cap * ratio
  max_borrow_fraction: 0.5         # each tracker may borrow up to 50% of its base cap
  smoothing_alpha: 0.4             # EMA alpha for effective cap smoothing
  min_effective_delta: 0.1         # min relative change to update effective cap
```

**Tracker Configuration** (customize for your trackers):
```yaml
trackers:
  - id: "premium"
    pattern: ".*premium-tracker\\.com.*"
    max_upload_speed: 10485760  # 10 MB/s
    priority: 10
    
  - id: "default" 
    pattern: ".*"  # Catch-all (must be last)
    max_upload_speed: -1  # unlimited/no cap for unmatched torrents
    priority: 1
```

### Tracker Matching & Limits
- Specific patterns first: The first matching tracker in your `trackers:` list wins. Put more specific regexes before broad ones. The catch‑all (`pattern: ".*"`) must be last.
- Catch‑all behavior: Torrents that don’t match a specific tracker map to the catch‑all. They use that tracker’s `max_upload_speed`.
- Unlimited per‑tracker: Set `max_upload_speed: -1` on any tracker (including the catch‑all) to apply no cap. The allocator will set per‑torrent upload limits to `-1` for that tracker.
- Switching to unlimited: If you change a tracker’s `max_upload_speed` from a finite value to `-1`, existing capped torrents on that tracker are flipped to unlimited on the next cycle.
- Specific vs default: If a torrent matches a specific tracker and the catch‑all, the specific tracker’s limit applies (order precedence).
- Torrents with multiple trackers: We query qBittorrent for a torrent’s trackers and use a single URL — the first “working” (status=2) tracker, else the first non‑error URL. Matching is performed on that single URL; we don’t aggregate across multiple tracker URLs for a torrent in Phase 1.

### Strategies

Qguardarr supports three strategies controlled by `global.allocation_strategy`:
- `equal` (Phase 1): equal split per tracker with a 10 KiB/s per‑torrent floor.
- `weighted` (Phase 2): proportional within‑tracker based on peers/speed; bounds: min 10 KiB/s, max 60% of tracker cap.
- `soft` (Phase 3): cross‑tracker borrowing of unused capacity, weighted by tracker priority, with smoothing to avoid oscillation.

Strategy selection (flow)
```
Start
  |
  |-- Prefer simplicity / one tracker / few torrents?  --> equal
  |
  |-- Many torrents per tracker; stronger should get more?  --> weighted
  |
  |-- Multiple trackers; some under-used, others starved?    --> soft
```

See the full Strategy Guide for examples and tuning: [STRATEGIES.md](STRATEGIES.md)

### Dry‑run Mode

Dry‑run lets you exercise the allocation logic safely against your live qBittorrent without applying any changes.

- Enable in `config/qguardarr.yaml` (global):
  ```yaml
  global:
    dry_run: true
    dry_run_store_path: ./data/dry_run_limits.json  # optional, default shown
  ```
- Behavior:
  - Reads real torrents and current limits from qBittorrent.
  - Computes proposed per‑torrent limits using the selected strategy (equal/weighted/soft).
  - Does NOT call qBittorrent to set limits and does NOT write rollback entries.
  - Logs a human‑friendly diff for each proposed change, e.g.
    `[DRY-RUN] abcd1234: 256.0 KiB/s -> 1.20 MiB/s`.
  - Persists simulated per‑torrent limits to a JSON store (default `data/dry_run_limits.json`).
  - Updates the in‑memory cache to reflect simulated limits so repeated identical changes are not proposed every cycle.
- Visibility:
  - `/health` includes `dry_run: true` when enabled.
  - `/stats` includes `dry_run: true` and the current `strategy`.
- JSON store usage:
  - It’s a simple key→value mapping `{ "<torrent-hash>": <limit-int> }` loaded into memory once and used for O(1) lookups (no line‑by‑line scanning).
  - On each dry‑run cycle, new simulated limits are merged and saved back to the same file.
  - To clear, stop the service and delete the file (or ask us to add a `/dry-run/clear` endpoint).


### Strategy Guide

For detailed examples, plain‑English explanations, safe defaults, and tuning tips, see: [STRATEGIES.md](STRATEGIES.md)

### Gradual Deployment Process

1. **Start conservative**: `rollout_percentage: 10`
2. **Monitor for 24-48 hours** - check logs, memory usage, API performance
3. **Increase gradually**: 25% → 50% → 75% → 100%
4. **Watch for issues**: High memory usage, API timeouts, qBittorrent instability

## Monitoring & Management

### Health Check
```bash
curl http://localhost:8089/health
```

### View Statistics
```bash
curl http://localhost:8089/stats
curl http://localhost:8089/stats/trackers
```

Stats payload notes:
- `managed_torrent_count`: number of torrents currently under active management (Phase 2 selection).
- `score_distribution`: counts of torrents by score bucket: `high` (>=0.8), `medium` (>=0.5), `low` (>=0.2), `ignored` (<0.2).
- `api_calls_last_cycle`, `last_cycle_duration`: quick health indicators for each allocation pass.
- `/stats/trackers` includes per-tracker:
  - configured_limit_mbps, active_torrents, current_usage_mbps
  - priority
  - effective_cap_mbps and borrowed_mbps (when strategy = soft)

### Preview Next Cycle (dry-run)
```bash
curl http://localhost:8089/preview/next-cycle | jq
```

The response includes:
- `strategy`: current allocation strategy
- `torrents_considered`: number of torrents included in calculation
- `proposed_count`: number of torrents whose limits would change
- `proposed_changes`: map of torrent hash -> proposed new limit (bytes/sec)
- `trackers`: per-tracker base_cap, effective_cap, and borrowed (bytes/sec)
 - `summary.trackers`: [{id, base_cap_mbps, base_cap_h, effective_cap_mbps, effective_cap_h, borrowed_mbps, borrowed_h}]
 - `summary.top_changes`: [{hash, new_limit_kib, new_limit_h, delta_kib, delta_h}] (top 10)

### View Current Config
```bash
curl http://localhost:8089/config | jq
```

Example (sanitized):
```json
{
  "global": {
    "update_interval": 300,
    "active_torrent_threshold_kb": 10,
    "max_api_calls_per_cycle": 500,
    "differential_threshold": 0.2,
    "rollout_percentage": 10,
    "host": "0.0.0.0",
    "port": 8089,
    "allocation_strategy": "equal",
    "max_managed_torrents": 1000
  },
  "qbittorrent": { "host": "localhost", "port": 8080, "username": "admin", "password": "***" },
  "cross_seed": { "enabled": false, "url": "http://localhost:2468/api/webhook", "api_key": "***" },
  "trackers": [ { "id": "default", "pattern": ".*", "max_upload_speed": 2097152, "priority": 1 } ],
  "rollback": { "database_path": "./data/rollback.db", "track_all_changes": true },
  "logging": { "level": "INFO", "file": "./logs/qguardarr.log" }
}
```

### Force Update Cycle
```bash
curl -XPOST http://localhost:8089/cycle/force
```

### Emergency Rollback
```bash
curl -XPOST http://localhost:8089/rollback \
  -H "Content-Type: application/json" \
  -d '{"confirm": true, "reason": "emergency"}'
```

### Increase Rollout
```bash
curl -XPOST http://localhost:8089/rollout \
  -H "Content-Type: application/json" \
  -d '{"percentage": 50}'
```

### Reset Limits (set to unlimited)

Set upload limits to unlimited (\-1) for torrents previously touched by Qguardarr.

Endpoint: `POST /limits/reset`

Body options:
- `confirm`: true (required)
- `scope`: `"unrestored"` (default) or `"all"`
  - `unrestored`: only torrents with rollback entries that have not been marked restored
  - `all`: all torrents that have any rollback history
- `mark_restored`: true|false (optional; default false)
  - If true, marks rollback entries for the affected torrents as restored (see below)

Behavior:
- Dry‑run: updates the dry‑run JSON store and in‑memory cache; does not call qBittorrent.
- Real mode: sets unlimited in qBittorrent (batched) and updates the cache.

Rollback interaction:
- `/rollback` re‑applies prior per‑torrent limits using entries where `restored = 0`.
- After a reset:
  - If you DO NOT pass `mark_restored: true`, existing rollback entries remain unrestored, so a later `/rollback` can restore pre‑reset limits.
  - If you DO pass `mark_restored: true`, those entries are marked restored, so `/rollback` will not change those torrents anymore (history remains for audit).

Examples
```bash
# Reversible reset (can be undone later with /rollback)
curl -XPOST http://localhost:8089/limits/reset \
  -H "Content-Type: application/json" \
  -d '{"confirm": true, "scope": "unrestored"}'

# Final reset for all touched torrents (prevents future rollbacks)
curl -XPOST http://localhost:8089/limits/reset \
  -H "Content-Type: application/json" \
  -d '{"confirm": true, "scope": "all", "mark_restored": true}'
```

## Performance Expectations (Phase 1)

| Metric | Target | Typical |
|--------|---------|---------|
| Memory Usage | <60MB | ~45MB |
| CPU Usage | <3% | ~1.5% |
| Update Cycle | <10s | ~3s |
| Webhook Response | <10ms | ~5ms |
| API Calls/Cycle | <300 | ~150 |

## Testing

### Makefile Shortcuts (recommended)
Use these convenience targets instead of calling tools directly:
```bash
# Unit tests (fast / with coverage)
make test-fast
make test

# Linting, type checks, formatting
make lint
make type-check
make format

# Docker-based integration tests
make test-docker-quick   # quick subset
make test-docker         # fuller suite
```

Notes
- Docker tests require Docker and Docker Compose (v1 or v2). The test harness auto-detects either `docker-compose` or `docker compose`.
- CI doesn’t run Docker tests by default on GitHub-hosted runners. You can enable them by setting a repo variable `RUN_DOCKER_TESTS=1`.

### Manual (advanced)
If you prefer to run things manually:
```bash
# Unit tests
pytest tests/unit/ -v

# Configuration sanity check
python -c "from src.config import ConfigLoader; ConfigLoader().load_config(); print('✅ Config valid')"
```

## Troubleshooting

### Common Issues

**Service won't start**:
- Check config file exists: `config/qguardarr.yaml`
- Validate config: `python -c "from src.config import ConfigLoader; ConfigLoader().load_config()"`
- Check qBittorrent connectivity

**High memory usage**:
- Reduce `rollout_percentage`
- Lower `active_torrent_threshold_kb`
- Check for memory leaks in logs

**Webhook timeouts**:
- Verify webhook URL: `http://localhost:8089/webhook`
- Check qBittorrent external command configuration
- Monitor response times in `/stats`

**API errors**:
- Verify qBittorrent credentials in config
- Check qBittorrent Web UI is accessible
- Review API call limits in config

### Log Locations
- **Docker**: `docker-compose logs qguardarr`
- **Direct**: `logs/qguardarr.log`

### Emergency Recovery
1. **Stop service**: `docker-compose down` or Ctrl+C
2. **Rollback all changes**: Use rollback endpoint before stopping
3. **Reset qBittorrent**: Restart qBittorrent to clear any stuck limits

## Development

### Common Commands
```bash
# Install dev deps
make install-dev

# Lint, type-check, unit tests
make lint && make type-check && make test

# Docker integration (full or quick)
make test-docker      # full
make test-docker-quick
```

### CI/CD
- GitHub Actions run linting, type-checking, unit tests, and quick Docker tests on PRs.
- Multi-arch Docker images (linux/amd64, linux/arm64) are built via Buildx and pushed to registry.

### Running Tests
```bash
pytest tests/ -v --cov=src
```

### Code Style
```bash
black src/ tests/
isort src/ tests/
mypy src/
```

## Next Steps

🚀 Phase 3: Soft limits with cross-tracker borrowing + priorities  
📊 Phase 4: Advanced monitoring and production polish  

---

**Support**: Create issues for bugs or feature requests  
**Contributing**: Pull requests welcome for enhancements
