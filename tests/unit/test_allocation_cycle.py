"""Tests to cover run_allocation_cycle and related paths."""

import pytest
from unittest.mock import AsyncMock, Mock

from src.allocation import AllocationEngine
from src.config import QguardarrConfig, GlobalSettings, QBittorrentSettings, TrackerConfig, RollbackSettings, CrossSeedSettings, LoggingSettings
from src.qbit_client import TorrentInfo


def make_config() -> QguardarrConfig:
    return QguardarrConfig(
        **{
            "global": GlobalSettings(
                update_interval=300,
                active_torrent_threshold_kb=10,
                max_api_calls_per_cycle=500,
                differential_threshold=0.2,
                rollout_percentage=100,
                host="localhost",
                port=8089,
            ),
            "qbittorrent": QBittorrentSettings(host="localhost", port=8080, username="u", password="p", timeout=10),
            "trackers": [
                TrackerConfig(id="default", name="Default", pattern=".*", max_upload_speed=2 * 1024 * 1024, priority=1)
            ],
            "rollback": RollbackSettings(database_path="./data/test.db", track_all_changes=True),
            "cross_seed": CrossSeedSettings(enabled=False),
            "logging": LoggingSettings(level="INFO", file="./logs/test.log", max_size_mb=10, backup_count=1),
        }
    )


def make_torrent(hash_: str) -> TorrentInfo:
    return TorrentInfo(
        hash=hash_, name="t", state="uploading", progress=1.0, dlspeed=0, upspeed=0, priority=0,
        num_seeds=1, num_leechs=1, ratio=1.0, size=100, completed=100, tracker="http://tracker/announce"
    )


@pytest.mark.asyncio
async def test_run_allocation_cycle_happy(monkeypatch):
    config = make_config()

    qbit = AsyncMock()
    # Active torrents returns one, all torrents returns same
    h1 = make_torrent("h1")
    qbit.get_torrents.side_effect = [[h1], [h1]]
    qbit.get_torrent_upload_limit = AsyncMock(return_value=500000)
    qbit.set_torrents_upload_limits_batch = AsyncMock()
    qbit.needs_update = Mock(return_value=True)

    # Simple tracker matcher: always default
    tracker_matcher = Mock()
    tracker_matcher.match_tracker.return_value = "default"
    tracker_matcher.get_tracker_config.return_value = Mock(max_upload_speed=2 * 1024 * 1024)
    tracker_matcher.get_cache_stats.return_value = {"cache_hits": 0, "cache_misses": 0, "cache_size": 0, "hit_rate_percent": 0.0, "pattern_matches": 0, "failed_matches": 0}

    rollback = AsyncMock()
    rollback.record_batch_changes = AsyncMock(return_value=1)

    engine = AllocationEngine(config, qbit, tracker_matcher, rollback)
    await engine.run_allocation_cycle()

    # Should have attempted to set limits
    qbit.set_torrents_upload_limits_batch.assert_called_once()
    st = engine.get_stats()
    assert st["active_torrents"] >= 1
    assert st["managed_torrents"] >= 1
    assert st["limits_applied"] >= 1


@pytest.mark.asyncio
async def test_run_allocation_cycle_exception(monkeypatch):
    config = make_config()
    qbit = AsyncMock()
    # Force _get_active_torrents to raise
    engine = AllocationEngine(config, qbit, Mock(), AsyncMock())

    async def boom(*args, **kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(engine, "_get_active_torrents", boom, raising=True)
    with pytest.raises(RuntimeError):
        await engine.run_allocation_cycle()


@pytest.mark.asyncio
async def test_get_active_torrents_cache_merge():
    config = make_config()
    qbit = AsyncMock()
    engine = AllocationEngine(config, qbit, Mock(), AsyncMock())

    # Preload cache with h2
    engine.cache.add_torrent("h2", "default", 0.0, 0)

    # Active returns empty; all returns h2
    qbit.get_torrents.side_effect = [[], [make_torrent("h2")]]
    out = await engine._get_active_torrents()
    assert any(t.hash == "h2" for t in out)


@pytest.mark.asyncio
async def test_update_cache_fetches_missing_limit():
    config = make_config()
    qbit = AsyncMock()
    tracker_matcher = Mock()
    tracker_matcher.match_tracker.return_value = "default"
    engine = AllocationEngine(config, qbit, tracker_matcher, AsyncMock())

    # No torrent in cache; should fetch limit once
    qbit.get_torrent_upload_limit = AsyncMock(return_value=1024)
    t = make_torrent("hx")
    await engine._update_cache([t])
    assert engine.cache.get_current_limit("hx") == 1024
    assert engine.stats["api_calls_last_cycle"] >= 1

