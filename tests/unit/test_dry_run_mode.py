"""Tests for dry-run mode: no writes to qBittorrent, simulated state persists."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


@pytest.mark.asyncio
async def test_dry_run_cycle_persists_limits(
    tmp_path: Path, test_config: QguardarrConfig, caplog
):
    # Enable dry run
    test_config.global_settings.dry_run = True
    test_config.global_settings.dry_run_store_path = str(tmp_path / "dry.json")

    # qbit client
    qbit = AsyncMock()
    torrents = [
        TorrentInfo(
            hash="h1",
            name="h1",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=100 * 1024,
            priority=1,
            num_seeds=10,
            num_leechs=5,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://x/announce",
        ),
        TorrentInfo(
            hash="h2",
            name="h2",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=200 * 1024,
            priority=1,
            num_seeds=3,
            num_leechs=2,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://x/announce",
        ),
    ]
    # active + cached
    qbit.get_torrents.side_effect = [torrents, torrents]
    qbit.get_torrent_upload_limit.return_value = -1

    # Ensure differential updates always apply in this test
    qbit.needs_update = Mock(return_value=True)

    # tracker matcher and config
    matcher = Mock()
    matcher.match_tracker.return_value = "X"
    matcher.get_tracker_config.return_value = Mock(
        id="X", name="X", max_upload_speed=2 * 1024 * 1024, priority=5
    )

    rollback = AsyncMock()

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )

    # Capture info logs
    import logging

    caplog.set_level(logging.INFO)

    # Run one allocation cycle; should log [DRY-RUN] and write simulated limits
    await engine.run_allocation_cycle()
    # No writes to qBittorrent
    assert qbit.set_torrents_upload_limits_batch.await_count == 0
    # Store file created
    store_path = Path(test_config.global_settings.dry_run_store_path)
    assert store_path.exists()
    data = json.loads(store_path.read_text())
    assert "h1" in data and "h2" in data

    # Next cycle should see simulated limits as current and not propose repeat changes
    await engine.run_allocation_cycle()
    # Still no writes
    assert qbit.set_torrents_upload_limits_batch.await_count == 0

    # Ensure logs contain DRY-RUN lines at least once
    assert any("[DRY-RUN]" in rec.message for rec in caplog.records)
