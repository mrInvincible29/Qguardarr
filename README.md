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

**Coming in Phase 2**: Soft limits with priority-based borrowing, smart torrent scoring

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

### Important Settings for Phase 1

**Safety Settings (start here)**:
```yaml
global:
  rollout_percentage: 10  # Start with 10% of torrents
  update_interval: 300    # Check every 5 minutes
  differential_threshold: 0.2  # Only update >20% changes
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

## Next Steps - Phase 2 & Beyond

🔄 **Phase 2** (Week 2): Smart torrent scoring and differential updates  
🚀 **Phase 3** (Week 3): Soft limits with priority-based borrowing  
📊 **Phase 4** (Week 4): Advanced monitoring and production polish  

---

**Support**: Create issues for bugs or feature requests  
**Contributing**: Pull requests welcome for enhancements
