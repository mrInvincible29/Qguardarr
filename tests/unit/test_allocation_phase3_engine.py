"""Engine integration test for Phase 3 soft strategy"""

from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


@pytest.mark.asyncio
async def test_engine_uses_soft_strategy_when_configured(
    test_config: QguardarrConfig,
):
    # Configure for soft strategy
    test_config.global_settings.allocation_strategy = "soft"

    qbit = AsyncMock()
    # Three active torrents across two trackers: X (cap 4MB), Y (cap 2MB)
    torrents = [
        TorrentInfo(
            hash="x1",
            name="x1",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=1800 * 1024,
            priority=1,
            num_seeds=20,
            num_leechs=10,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://x/announce",
        ),
        TorrentInfo(
            hash="x2",
            name="x2",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=1700 * 1024,
            priority=1,
            num_seeds=10,
            num_leechs=5,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://x/announce",
        ),
        TorrentInfo(
            hash="y1",
            name="y1",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=200 * 1024,  # under-utilized Y
            priority=1,
            num_seeds=1,
            num_leechs=1,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://y/announce",
        ),
    ]

    # active + cached
    qbit.get_torrents.side_effect = [torrents, torrents]
    qbit.get_torrent_upload_limit.return_value = -1
    qbit.set_torrents_upload_limits_batch.return_value = None

    matcher = Mock()
    matcher.match_tracker.side_effect = lambda url: (
        "X" if "/x/" in url or url.startswith("http://x") else "Y"
    )
    matcher.get_tracker_config.side_effect = lambda tid: Mock(
        id=tid,
        max_upload_speed=(4 * 1024 * 1024 if tid == "X" else 2 * 1024 * 1024),
        priority=(10 if tid == "X" else 5),
    )

    rollback = AsyncMock()

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )

    await engine.run_allocation_cycle()

    # Verify batch update called and sums look sane
    assert qbit.set_torrents_upload_limits_batch.await_count == 1
    args, _ = qbit.set_torrents_upload_limits_batch.await_args
    limits = args[0]

    # Only X has 2 torrents; ensure both present
    assert set(limits.keys()).issuperset({"x1", "x2"})

    x_total = limits["x1"] + limits["x2"]
    # X base cap is 4MB; Y provides pool, but borrow capped at 50% of base (2MB)
    assert x_total >= 4 * 1024 * 1024
    assert x_total <= 6 * 1024 * 1024
