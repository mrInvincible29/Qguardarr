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

New in Phase 2 (optional): Weighted within-tracker allocation with smart torrent scoring. Defaults remain Phase 1 equal-split unless enabled.

## Quick Start

### Option 1: Docker (Recommended)

1. **Clone and configure**:
   ```bash
   git clone <repository>
   cd qguardarr
   cp config/qguardarr.yaml.example config/qguardarr.yaml
   cp .env.example .env
   ```

2. **Edit configuration**:
   - Update `config/qguardarr.yaml` with your tracker patterns and limits
   - Update `.env` with your qBittorrent credentials

3. **Start the service**:
   ```bash
   docker-compose up -d
   ```

4. **Configure qBittorrent webhook**:
   In qBittorrent → Options → Downloads → "Run external program on torrent completion":
   ```bash
   curl -XPOST http://localhost:8089/webhook \
     --data-urlencode "event=complete" \
     --data-urlencode "hash=%I" \
     --data-urlencode "name=%N" \
     --data-urlencode "tracker=%T"
   ```

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
    max_upload_speed: 2097152  # 2 MB/s (-1 for unlimited/no cap)
    priority: 1
```

### Tracker Matching & Limits
- Specific patterns first: The first matching tracker in your `trackers:` list wins. Put more specific regexes before broad ones. The catch‑all (`pattern: ".*"`) must be last.
- Catch‑all behavior: Torrents that don’t match a specific tracker map to the catch‑all. They use that tracker’s `max_upload_speed`.
- Unlimited per‑tracker: Set `max_upload_speed: -1` on any tracker (including the catch‑all) to apply no cap. The allocator will set per‑torrent upload limits to `-1` for that tracker.
- Switching to unlimited: If you change a tracker’s `max_upload_speed` from a finite value to `-1`, existing capped torrents on that tracker are flipped to unlimited on the next cycle.
- Specific vs default: If a torrent matches a specific tracker and the catch‑all, the specific tracker’s limit applies (order precedence).
- Torrents with multiple trackers: We query qBittorrent for a torrent’s trackers and use a single URL — the first “working” (status=2) tracker, else the first non‑error URL. Matching is performed on that single URL; we don’t aggregate across multiple tracker URLs for a torrent in Phase 1.

### Phase 2 Weighted Strategy

- Scoring: torrents get a 0–1 score using current upload, recent activity, and peers.
- Selection: only top `max_managed_torrents` are actively managed.
- Allocation: each tracker’s cap is distributed proportionally by score.
- Bounds: per-torrent min 10 KB/s; per-torrent max 60% of tracker cap.
- Defaults: Phase 1 equal-split remains the default; turn on via `allocation_strategy: weighted`.

### Phase 3 Soft Limits (Borrowing + Smoothing)

- Borrowing: unused capacity across trackers forms a pool. Trackers near/over their base cap borrow from this pool, weighted by tracker `priority`.
- Caps: borrowing per tracker is capped by `max_borrow_fraction` of its base cap (default 50%).
- Smoothing: effective tracker caps are smoothed with EMA (`smoothing_alpha`) and a minimal change gate (`min_effective_delta`) to avoid churn.
- Strategy: enable via `allocation_strategy: soft` in `global`.

Which strategy should I use?
----------------------------

- Example assumptions for all scenarios below
  - Upstream link speed: 100 MiB/s. Per‑tracker caps in the examples are well below this link so the link itself is not the limiting factor.
  - Catch‑all (default) tracker: pattern ".*" at the end; any torrent that doesn’t match a specific tracker maps to this default. In the examples, the default is unlimited (max_upload_speed: -1), so torrents mapped to it get unlimited per‑torrent upload (‑1).

- equal (Phase 1)
  - Best for: simple setups, few torrents, strict per‑tracker caps, lowest complexity.
  - How it works: equal split of a tracker's cap across its active torrents (with a small per‑torrent minimum floor).
  - Advantages: predictable, easy to reason about, minimal churn/API calls.
  - Drawbacks: can under‑utilize capacity when many torrents are weak (each still gets a small slice).
  - Example (with catch‑all):
    - T (cap 4 MiB/s) has 4 active torrents → each ≈ 1.00 MiB/s.
    - Default (unlimited) has 2 active torrents that didn’t match any specific tracker → both torrents are set to unlimited (‑1 per‑torrent limit).
    - If T has 400 torrents, equal share ≈ 10 KiB/s but the floor is 10 KiB/s; many torrents sit at the floor and T may not fully saturate if peers are weak. Torrents on the unlimited default remain uncapped.

- weighted (Phase 2)
  - Best for: trackers with many concurrent torrents where you want the healthier torrents (more peers / more current upload) to get more.
  - How it works: allocates a tracker's cap proportionally to a simple score (peers ×0.6 + current speed ×0.4), with per‑torrent min 10 KiB/s and per‑torrent max 60% of tracker cap.
  - Advantages: better within‑tracker efficiency; strong torrents get larger shares while weak torrents keep a minimum.
  - Drawbacks: more dynamic; no cross‑tracker borrowing (unused bandwidth on one tracker can’t help others).
  - Example (with catch‑all):
    - Tracker T cap 6 MiB/s, two torrents:
      - A: 40 peers, 0.8 MiB/s now; B: 5 peers, 0.2 MiB/s.
      - Scores ≈ A: 0.6×(40/20→1.0) + 0.4×(0.8/1.0→0.8) = 0.6 + 0.32 = 0.92.
      - B: 0.6×(5/20→0.25) + 0.4×(0.2/1.0→0.2) = 0.15 + 0.08 = 0.23.
      - Total score 1.15. A ≈ (0.92/1.15)×6 = 4.80 MiB/s, B ≈ 1.20 MiB/s (both within per‑torrent max 60% = 3.6 MiB/s → A capped to 3.6 MiB/s, extra redistributed to B).
    - Default tracker (unlimited) has one torrent D that didn’t match any specific tracker → D is set to unlimited (‑1). For unlimited trackers, within‑tracker weighting is skipped by design.

- soft (Phase 3)
  - Best for: multi‑tracker setups where total bandwidth fluctuates and you want to reuse unused capacity across trackers, with priorities.
  - How it works:
    - Compute each tracker’s base cap usage; unused capacity forms a global pool.
    - Trackers near/over their base cap qualify to borrow. Borrowing shares are weighted by tracker priority × need.
    - Each tracker’s borrowing is capped (max_borrow_fraction × base cap). Effective caps are smoothed (EMA) and have a minimum relative change gate to avoid oscillations.
  - Advantages: highest overall utilization; priorities bias important trackers; smoothing reduces churn; preview endpoint helps inspect changes first.
- Drawbacks: more moving parts; effective caps vary over time; knobs (borrow_threshold_ratio, max_borrow_fraction, smoothing_alpha, min_effective_delta) require tuning.
  - Example (with catch‑all):
    - Trackers:
      - A (specific): base 4 MiB/s, currently using 1 MiB/s → 3 MiB/s unused.
      - B (specific): base 2 MiB/s, wants ~3 MiB/s (near/over cap), priority 10.
      - Default (catch‑all): unlimited (‑1). Torrents mapped here remain uncapped and do not participate in borrowing.
    - Pool = 3 MiB/s from A (its leftover). With borrow_threshold_ratio=0.9 and max_borrow_fraction=0.5:
      - Only B is eligible to borrow (default is unlimited, excluded from borrowing logic).
      - B’s share ≈ min(pool, 0.5×base_B) = min(3.0, 1.0) = 1.0 MiB/s.
      - Effective caps: B = 2.0 + 1.0 = 3.0 MiB/s. A remains at 4 MiB/s base. Default stays unlimited (uncapped per‑torrent).
      - Smoothing tempers sudden swings; tiny changes (under the delta gate) are ignored so limits don’t flap.

Soft in 30 seconds (plain‑English)
----------------------------------

- Think of each tracker as a tap with a labelled flow (its base cap).
- If a tap isn’t using all its flow, the leftover goes to a shared bucket.
- Busy taps can borrow from that bucket to pour faster for a while.
- Priorities decide who gets more from the bucket when several taps are busy.
- Safety rails: no tap can borrow too much, and flows change gradually (not jerky).

Three quick scenarios (no math)
--------------------------------

- One tracker quiet, one busy: the busy tracker temporarily gets a boost; when the quiet tracker wakes up, the boost shrinks back.
- Two busy trackers, different priorities: both get a boost, the higher‑priority one gets a larger share of the boost.
- All trackers busy: there’s no leftover to borrow, so everyone runs at (about) their base caps; soft behaves like weighted/equal here.

When to enable soft
-------------------

- You see unused bandwidth on some trackers while others are starved.
- You want a simple “borrow leftovers, but safely” behavior.
- You care that important trackers get first dibs (set priorities).

Safety defaults (good starting values)
-------------------------------------

- borrow_threshold_ratio: 0.9 (only trackers using ≳90% of base cap try to borrow)
- max_borrow_fraction: 0.5 (no tracker can borrow more than half its own cap)
- smoothing_alpha: 0.4 (caps change smoothly)
- min_effective_delta: 0.1 (ignore tiny changes)

Quick guidance
--------------

- Start with `equal` for initial rollout and safety.
- If some torrents need more than others within the same tracker, try `weighted`.
- If you have multiple trackers and often leave bandwidth on the table, use `soft` to borrow unused capacity safely (tune the knobs).
- Use `/preview/next-cycle` before switching strategies or changing knobs; increase `rollout_percentage` gradually.

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
