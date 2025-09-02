"""Engine integration test for Phase 2 weighted strategy"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


@pytest.mark.asyncio
async def test_engine_uses_weighted_strategy_when_configured(
    test_config: QguardarrConfig,
):
    # Configure for weighted strategy
    test_config.global_settings.allocation_strategy = "weighted"

    qbit = AsyncMock()
    # Two active torrents
    torrents = [
        TorrentInfo(
            hash="h1",
            name="t1",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=800 * 1024,
            priority=1,
            num_seeds=20,
            num_leechs=10,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://x/announce",
        ),
        TorrentInfo(
            hash="h2",
            name="t2",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=200 * 1024,
            priority=1,
            num_seeds=2,
            num_leechs=3,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://x/announce",
        ),
    ]

    qbit.get_torrents.side_effect = [
        [torrents[0], torrents[1]],
        torrents,
    ]  # active + cached
    qbit.get_torrent_upload_limit.return_value = -1
    qbit.set_torrents_upload_limits_batch.return_value = None

    matcher = Mock()
    matcher.match_tracker.return_value = "trackerX"
    matcher.get_tracker_config.return_value = Mock(
        id="trackerX", max_upload_speed=6 * 1024 * 1024, priority=5
    )

    rollback = AsyncMock()

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )

    await engine.run_allocation_cycle()

    # Verify batch update called with two hashes and sane limits
    assert qbit.set_torrents_upload_limits_batch.await_count == 1
    args, _ = qbit.set_torrents_upload_limits_batch.await_args
    limits = args[0]
    assert set(limits.keys()) == {"h1", "h2"}
    # Sum equals tracker cap
    assert sum(limits.values()) == 6 * 1024 * 1024
    # Proportional: h1 > h2
    assert limits["h1"] > limits["h2"]
