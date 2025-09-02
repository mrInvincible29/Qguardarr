"""Branch coverage tests for Phase 3 preview calculation."""

from typing import List
from unittest.mock import Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


def _ti(hash_: str, up_kib: int, peers: int, tracker: str) -> TorrentInfo:
    return TorrentInfo(
        hash=hash_,
        name=hash_,
        state="uploading",
        progress=1.0,
        dlspeed=0,
        upspeed=up_kib * 1024,
        priority=1,
        num_seeds=peers // 2,
        num_leechs=peers - peers // 2,
        ratio=1.0,
        size=1000,
        completed=1000,
        tracker=tracker,
    )


@pytest.mark.asyncio
async def test_phase3_preview_equal_split_and_remaining(test_config: QguardarrConfig):
    # Strategy soft
    test_config.global_settings.allocation_strategy = "soft"
    # Small cap for tracker X
    matcher = Mock()
    matcher.match_tracker.return_value = "X"
    matcher.get_tracker_config.side_effect = lambda tid: (
        Mock(id="X", name="X", max_upload_speed=1 * 1024 * 1024, priority=5)
        if tid == "X"
        else None
    )
    matcher.get_all_tracker_configs.return_value = [matcher.get_tracker_config("X")]

    engine = AllocationEngine(
        config=test_config,
        qbit_client=Mock(),
        tracker_matcher=matcher,
        rollback_manager=Mock(),
    )

    # Two torrents with zero scores (peers=0, up=0) → equal split branch
    torrents: List[TorrentInfo] = [
        _ti("x1", 0, 0, "http://x"),
        _ti("x2", 0, 0, "http://x"),
    ]

    limits, trackers = engine._calculate_phase3_preview(torrents)  # type: ignore[attr-defined]

    # Both present with at least floor 10 KiB/s
    assert set(limits.keys()) == {"x1", "x2"}
    assert all(v >= 10 * 1024 for v in limits.values())
    # Tracker preview contains base/effective/borrowed
    assert "X" in trackers
    assert trackers["X"]["base_cap"] == 1 * 1024 * 1024
    assert isinstance(trackers["X"]["effective_cap"], int)


@pytest.mark.asyncio
async def test_phase3_preview_reduce_branch_and_smoothing(test_config: QguardarrConfig):
    # Strategy soft
    test_config.global_settings.allocation_strategy = "soft"
    matcher = Mock()
    matcher.match_tracker.return_value = "Y"
    matcher.get_tracker_config.side_effect = lambda tid: (
        Mock(id="Y", name="Y", max_upload_speed=1 * 1024 * 1024, priority=5)
        if tid == "Y"
        else None
    )
    matcher.get_all_tracker_configs.return_value = [matcher.get_tracker_config("Y")]

    engine = AllocationEngine(
        config=test_config,
        qbit_client=Mock(),
        tracker_matcher=matcher,
        rollback_manager=Mock(),
    )

    # Seed smoothing previous effective cap so preview goes through EMA path
    engine._last_effective_caps["Y"] = float(1 * 1024 * 1024)

    # Create many torrents to force total_alloc > cap (reduce branch)
    torrents: List[TorrentInfo] = [_ti(f"y{i}", 0, 0, "http://y") for i in range(200)]

    limits, trackers = engine._calculate_phase3_preview(torrents)  # type: ignore[attr-defined]

    # Many entries present and reduced so that sum is close to cap and no torrent below floor
    assert len(limits) == 200
    # When many torrents hit the 10 KiB/s floor, we can't reduce below cap; values stay at floor
    assert all(v == 10 * 1024 for v in limits.values())
    # EMA path used: effective_cap is an int near 1 MiB
    assert isinstance(trackers["Y"]["effective_cap"], int)


@pytest.mark.asyncio
async def test_phase3_preview_unlimited_tracker(test_config: QguardarrConfig):
    # Strategy soft
    test_config.global_settings.allocation_strategy = "soft"
    matcher = Mock()
    matcher.match_tracker.return_value = "D"
    matcher.get_tracker_config.side_effect = lambda tid: (
        Mock(id="D", name="Default", max_upload_speed=-1, priority=1)
        if tid == "D"
        else None
    )
    matcher.get_all_tracker_configs.return_value = [matcher.get_tracker_config("D")]

    engine = AllocationEngine(
        config=test_config,
        qbit_client=Mock(),
        tracker_matcher=matcher,
        rollback_manager=Mock(),
    )

    torrents = [_ti("d1", 100, 2, "http://d")]
    limits, trackers = engine._calculate_phase3_preview(torrents)  # type: ignore[attr-defined]

    # Unlimited tracker sets -1 per torrent
    assert limits["d1"] == -1
    assert trackers["D"]["effective_cap"] == -1


@pytest.mark.asyncio
async def test_phase3_preview_reduce_with_reducible(test_config: QguardarrConfig):
    # Strategy soft
    test_config.global_settings.allocation_strategy = "soft"
    matcher = Mock()
    matcher.match_tracker.return_value = "Z"
    cap = 1 * 1024 * 1024
    matcher.get_tracker_config.side_effect = lambda tid: (
        Mock(id="Z", name="Z", max_upload_speed=cap, priority=5) if tid == "Z" else None
    )
    matcher.get_all_tracker_configs.return_value = [matcher.get_tracker_config("Z")]

    engine = AllocationEngine(
        config=test_config,
        qbit_client=Mock(),
        tracker_matcher=matcher,
        rollback_manager=Mock(),
    )

    # One strong torrent plus many at floor; total_alloc > cap, reducible > 0 from the strong torrent
    torrents: List[TorrentInfo] = [
        _ti("z-strong", 800, 40, "http://z"),
    ] + [_ti(f"z{i}", 0, 0, "http://z") for i in range(200)]

    limits, _ = engine._calculate_phase3_preview(torrents)  # type: ignore[attr-defined]

    strong = limits["z-strong"]
    # Strong torrent is below the 60% cap due to reduction to accommodate floor for others
    assert strong < int(0.6 * cap)
    # Others stay at floor
    floors = [limits[h] for h in limits if h != "z-strong"]
    assert all(v == 10 * 1024 for v in floors)
