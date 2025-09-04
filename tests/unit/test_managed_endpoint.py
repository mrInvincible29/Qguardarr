import time

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_stats_managed_endpoint(monkeypatch):
    import src.main as main
    from src.allocation import AllocationEngine
    from src.config import (
        CrossSeedSettings,
        GlobalSettings,
        LoggingSettings,
        QBittorrentSettings,
        QguardarrConfig,
        RollbackSettings,
        TrackerConfig,
    )

    # Minimal real engine with in-memory cache
    cfg = QguardarrConfig(
        **{
            "global": GlobalSettings(),
            "qbittorrent": QBittorrentSettings(
                host="h", port=8080, username="u", password="p"
            ),
            "cross_seed": CrossSeedSettings(enabled=False),
            "trackers": [
                TrackerConfig(
                    id="default",
                    name="d",
                    pattern=".*",
                    max_upload_speed=-1,
                    priority=1,
                )
            ],
            "rollback": RollbackSettings(database_path="./data/rb.db"),
            "logging": LoggingSettings(),
        }
    )

    class DummyQbit:
        pass

    class DummyMatcher:
        def get_cache_stats(self):
            return {
                "cache_hits": 0,
                "cache_misses": 0,
                "cache_size": 0,
                "hit_rate_percent": 0.0,
                "pattern_matches": 0,
                "failed_matches": 0,
            }

    engine = AllocationEngine(cfg, DummyQbit(), DummyMatcher(), None)  # type: ignore[arg-type]
    # Add a torrent to cache
    t0 = int(time.time())
    engine.cache.add_torrent("h1", "default", upload_speed=0.0, current_limit=1024)

    main.app_state["allocation_engine"] = engine

    client = TestClient(main.app)
    r = client.get("/stats/managed")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert "default" in data["counts_by_tracker"]
    items = data["trackers"]["default"]
    assert any(it["hash"] == "h1" and it["current_limit"] == 1024 for it in items)
    # added_at is present and >= t0
    added = next(it["added_at"] for it in items if it["hash"] == "h1")
    assert added is None or added >= t0
