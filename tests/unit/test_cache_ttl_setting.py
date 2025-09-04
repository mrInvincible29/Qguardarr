import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

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


def make_cfg(ttl: int) -> QguardarrConfig:
    return QguardarrConfig(
        **{
            "global": GlobalSettings(
                update_interval=300,
                active_torrent_threshold_kb=10,
                cache_ttl_seconds=ttl,
            ),
            "qbittorrent": QBittorrentSettings(
                host="localhost", port=8080, username="u", password="p", timeout=10
            ),
            "trackers": [
                TrackerConfig(
                    id="default",
                    name="Default",
                    pattern=".*",
                    max_upload_speed=-1,
                    priority=1,
                )
            ],
            "rollback": RollbackSettings(database_path="./data/test.db"),
            "cross_seed": CrossSeedSettings(enabled=False),
            "logging": LoggingSettings(level="INFO", file="./logs/test.log"),
        }
    )


@pytest.mark.asyncio
async def test_cleanup_uses_config_ttl(monkeypatch):
    cfg = make_cfg(1234)
    qbit = AsyncMock()
    engine = AllocationEngine(cfg, qbit, Mock(), AsyncMock())

    # No active torrents
    qbit.get_torrents.return_value = []

    called = {"ttl": None}

    def fake_cleanup(max_age_seconds: int = 1800):
        called["ttl"] = max_age_seconds
        return 0

    monkeypatch.setattr(
        engine.cache, "cleanup_old_torrents", fake_cleanup, raising=True
    )

    await engine.run_allocation_cycle()
    assert called["ttl"] == 1234
