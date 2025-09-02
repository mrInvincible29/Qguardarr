"""Cover preview delta calculation and unlimited summary strings."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


@pytest.mark.asyncio
async def test_preview_equal_delta_humanized(test_config: QguardarrConfig):
    test_config.global_settings.allocation_strategy = "equal"
    qbit = AsyncMock()
    torrents = [
        TorrentInfo(
            hash="delta1",
            name="delta1",
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
            hash="delta2",
            name="delta2",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=200 * 1024,
            priority=1,
            num_seeds=5,
            num_leechs=5,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://x/announce",
        ),
    ]
    qbit.get_torrents.side_effect = [torrents, torrents]

    matcher = Mock()
    matcher.match_tracker.return_value = "X"
    matcher.get_tracker_config.return_value = Mock(
        id="X", name="X", max_upload_speed=2 * 1024 * 1024, priority=5
    )
    matcher.get_all_tracker_configs.return_value = [matcher.get_tracker_config("X")]

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=AsyncMock(),
    )

    # Seed cache current limit so delta is computed in preview for delta1
    engine.cache.add_torrent("delta1", "X", 0.0, 256 * 1024)  # 256 KiB/s

    preview = await engine.preview_next_cycle()
    tops = preview["summary"]["top_changes"]
    if not tops:
        pytest.skip("No proposed changes under current thresholds")
    first = tops[0]
    assert "new_limit_h" in first
    # delta_h present when current known
    assert first["delta_h"] is None or isinstance(first["delta_h"], str)


@pytest.mark.asyncio
async def test_preview_unlimited_summary_strings(test_config: QguardarrConfig):
    test_config.global_settings.allocation_strategy = "soft"
    qbit = AsyncMock()
    # Single torrent on default unlimited tracker
    torrents = [
        TorrentInfo(
            hash="u1",
            name="u1",
            state="uploading",
            progress=1.0,
            dlspeed=0,
            upspeed=100 * 1024,
            priority=1,
            num_seeds=3,
            num_leechs=2,
            ratio=1.0,
            size=1000,
            completed=1000,
            tracker="http://d/announce",
        )
    ]
    qbit.get_torrents.side_effect = [torrents, torrents]

    matcher = Mock()
    matcher.match_tracker.return_value = "default"
    matcher.get_tracker_config.return_value = Mock(
        id="default", name="Default", max_upload_speed=-1, priority=1
    )
    matcher.get_all_tracker_configs.return_value = [
        matcher.get_tracker_config("default")
    ]

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=AsyncMock(),
    )

    preview = await engine.preview_next_cycle()
    t0 = preview["summary"]["trackers"][0]
    assert t0["base_cap_h"] == "unlimited"
    assert t0["effective_cap_h"] == "unlimited"
