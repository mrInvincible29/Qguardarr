# Qguardarr — qBittorrent Per‑Tracker Upload Limiter

Per‑tracker upload caps for qBittorrent with minimal API load, automatic config hot‑reload, and safe rollback.

## Highlights

- Per‑tracker caps with efficient batching and differential updates
- Active‑only selection (uses `filter=active`) + upspeed threshold
- Automatic config hot‑reload (file watcher) + manual reload endpoint
- Rollback and reset endpoints for safe recovery
- Pattern tester endpoint to validate tracker regexes quickly
- Managed listing endpoint to see what’s being controlled
- Docker‑first, GHCR images; simple Compose setup

## Quick Start (Docker)

1) Create a working folder
   ```bash
   mkdir -p qguardarr/{config,data,logs}
   cd qguardarr
   ```

2) Fetch Compose file from repo
   ```bash
   # docker-compose.yml
   curl -sSLO https://raw.githubusercontent.com/mrInvincible29/Qguardarr/main/docker-compose.yml
   # Optional Mac/Windows overrides
   curl -sSLO https://raw.githubusercontent.com/mrInvincible29/Qguardarr/main/docker-compose.override.yml
   ```

3) Create `.env` (or download example)
   ```bash
   # Quick create
   cat > .env << 'EOF'
   QBIT_HOST=host.docker.internal
   QBIT_PORT=8080
   QBIT_USERNAME=admin
   QBIT_PASSWORD=your_password_here
   CROSS_SEED_URL=http://host.docker.internal:2468/api/webhook
   CROSS_SEED_API_KEY=
   APP_PORT=8089
   EOF
   # Or: curl -sSLo .env https://raw.githubusercontent.com/mrInvincible29/Qguardarr/main/.env.example
   ```

4) Get config and edit
   ```bash
   curl -sSLo config/qguardarr.yaml \
     https://raw.githubusercontent.com/mrInvincible29/Qguardarr/main/config/qguardarr.yaml.example
   # Then open config/qguardarr.yaml and customize trackers and limits
   ```

5) Start
   ```bash
   docker compose up -d
   # or: docker-compose up -d
   ```

6) Optional: qBittorrent webhook (faster reaction)
    qBittorrent → Options → Downloads → “Run external program on torrent completion”:
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

## Configuration (essentials)

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

Global defaults that matter most:
- `global.rollout_percentage`: default 100 when omitted. Lower it to test safely.
- `global.active_torrent_threshold_kb`: minimum upspeed (KiB/s) a torrent must have to be considered.
- `trackers[*].max_upload_speed`: bytes/sec (use `-1` for unlimited/no cap).
- `global.cache_ttl_seconds`: how long to keep inactive torrents in the in‑memory cache (default 1800s). Inactive torrents do not count toward sharing; they are cleaned up after this TTL.

Example trackers (order matters; specific before catch‑all):
```yaml
trackers:
  - id: "private1"
    name: "Private Tracker A"
    pattern: ".*private-tracker\\.example\\.org.*"
    max_upload_speed: 10485760  # 10 MB/s
    priority: 2

  - id: "custom1"
    name: "Custom Tracker B"
    pattern: ".*tracker-b\\.example\\.net.*"
    max_upload_speed: 2097152  # 2 MB/s
    priority: 3

  - id: "default"
    name: "All Others"
    pattern: ".*"   # Catch-all (must be last)
    max_upload_speed: -1
    priority: 1
```

Pattern tips
- Use `.*domain\.tld.*` (not `.domain\.tld.`). Dots must be escaped and `.*` added.
- Subdomains (e.g., tracker.private-tracker.example.org) are matched by `.*private-tracker\.example\.org.*`.
- Multiple variants: add multiple tracker entries (specific wins by order).
- Anchors: if you need exact control, use `^...$`; otherwise Qguardarr normalizes simple patterns by wrapping with `.*`.
- Test quickly: `GET /match/test?url=<tracker_url>&detailed=true`.

## Endpoints

- `GET /health` — service health and basic stats
- `GET /config` — current (sanitized) config
- `POST /config/reload` — apply config file changes immediately
- `GET /stats` — detailed stats (includes strategy, dry‑run, cache stats)
- `GET /stats/trackers` — per‑tracker stats: configured_limit_mbps, active_torrents, current_usage_mbps, effective_cap_mbps, borrowed_mbps, efficiency_percent
- `GET /stats/managed` — managed torrents grouped by tracker: [{hash, current_limit, added_at, last_seen, age_seconds}]
- `GET /preview/next-cycle` — compute proposed changes without applying
- `POST /cycle/force` — run an allocation cycle now
- `POST /rollout` body `{ "percentage": 50 }` — change rollout percentage
- `POST /rollback` body `{ "confirm": true, "reason": "..." }` — restore original limits from rollback DB
- `POST /limits/reset` body `{ "confirm": true, "scope": "unrestored"|"all", "mark_restored": false }` — set previously touched torrents to unlimited
- `POST /smoothing/reset` body `{ "all": true }` or `{ "tracker_id": "id" }` — reset soft‑strategy smoothing state
- `POST /webhook` — accept qBittorrent events (e.g., `event=complete&hash=...&tracker=...`)
- `GET /match/test?url=<tracker_url>&detailed=true` — test a URL against configured patterns

## Dry‑run mode

Enable safe simulation without touching qBittorrent:
```yaml
global:
  dry_run: true
  dry_run_store_path: ./data/dry_run_limits.json
```
Logs show `[DRY-RUN]` diffs; `/stats` and `/health` display `dry_run: true`.


### Strategy Guide

For detailed examples, plain‑English explanations, safe defaults, and tuning tips, see: [STRATEGIES.md](STRATEGIES.md)

## Operations

- Hot‑reload: saving `config/qguardarr.yaml` is auto‑applied within a few seconds; or call `POST /config/reload`.
- Logs: application logs → `logs/qguardarr.log`; Uvicorn writes console logs.
- Force cycle: `POST /cycle/force`.
- Change rollout: `POST /rollout {"percentage": 100}`.
- Inspect managed set: `GET /stats/managed`.

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

## Docker notes

- The Compose file maps `APP_PORT` to the container and health check; set it in `.env` to match `global.port` (default 8089).
- On macOS/Windows, use `host.docker.internal` to reach host qBittorrent; on Linux, use `localhost` or the host IP.

## API Cheatsheet (curl)

- Health: `curl http://localhost:8089/health | jq`
- Trackers: `curl http://localhost:8089/stats/trackers | jq`
- Managed: `curl http://localhost:8089/stats/managed | jq`
- Preview: `curl http://localhost:8089/preview/next-cycle | jq`
- Force cycle: `curl -XPOST http://localhost:8089/cycle/force | jq`
- Rollout 50%: `curl -XPOST http://localhost:8089/rollout -H 'Content-Type: application/json' -d '{"percentage":50}'`
- Reset limits (unrestored): `curl -XPOST http://localhost:8089/limits/reset -H 'Content-Type: application/json' -d '{"confirm":true}'`
- Match test: `curl 'http://localhost:8089/match/test?url=http://tracker.example.com/announce&detailed=true' | jq`

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

---
Support: please open issues for bugs/feature requests  
Contributions welcome
