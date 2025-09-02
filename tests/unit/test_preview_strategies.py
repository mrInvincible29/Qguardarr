"""Preview endpoint engine-level tests for equal and weighted strategies."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


@pytest.mark.asyncio
async def test_preview_equal_strategy(test_config: QguardarrConfig):
    test_config.global_settings.allocation_strategy = "equal"

    qbit = AsyncMock()
    torrents = [
        TorrentInfo(
            hash="e1",
            name="e1",
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
            hash="e2",
            name="e2",
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

    preview = await engine.preview_next_cycle()
    assert preview["strategy"] == "equal"
    assert preview["torrents_considered"] == 2
    assert "summary" in preview


@pytest.mark.asyncio
async def test_preview_weighted_strategy(test_config: QguardarrConfig):
    test_config.global_settings.allocation_strategy = "weighted"

    qbit = AsyncMock()
    torrents = [
        TorrentInfo(
            hash="w1",
            name="w1",
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
            tracker="http://y/announce",
        ),
        TorrentInfo(
            hash="w2",
            name="w2",
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
            tracker="http://y/announce",
        ),
    ]
    qbit.get_torrents.side_effect = [torrents, torrents]

    matcher = Mock()
    matcher.match_tracker.return_value = "Y"
    matcher.get_tracker_config.return_value = Mock(
        id="Y", name="Y", max_upload_speed=6 * 1024 * 1024, priority=5
    )
    matcher.get_all_tracker_configs.return_value = [matcher.get_tracker_config("Y")]

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=AsyncMock(),
    )

    preview = await engine.preview_next_cycle()
    assert preview["strategy"] == "weighted"
    assert preview["torrents_considered"] == 2
    # stronger torrent should receive more in proposed changes
    pc = preview["proposed_changes"]
    assert pc["w1"] > pc["w2"]
