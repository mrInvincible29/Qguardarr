"""Unit tests for Phase 2 weighted allocator within trackers"""

from typing import List
from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


@pytest.fixture
def engine(test_config: QguardarrConfig) -> AllocationEngine:
    qbit = AsyncMock()
    matcher = Mock()
    # Use a specific tracker id for these tests
    matcher.match_tracker.side_effect = lambda url: "trackerX"
    matcher.get_tracker_config.side_effect = lambda tracker_id: Mock(
        id=tracker_id,
        max_upload_speed=(
            6 * 1024 * 1024 if tracker_id == "trackerX" else 1 * 1024 * 1024
        ),
        priority=5,
        name="TrackerX",
    )
    rollback = AsyncMock()

    return AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )


def _t(
    hash_: str, up_kib: int, peers: int, tracker: str = "http://x/announce"
) -> TorrentInfo:
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


def test_weighted_single_torrent_gets_full_limit(engine: AllocationEngine):
    torrents: List[TorrentInfo] = [_t("h1", up_kib=100, peers=10)]

    limits = engine._calculate_limits_phase2(torrents)  # type: ignore[attr-defined]

    # TrackerX limit is 6MB/s
    assert limits["h1"] == 6 * 1024 * 1024


def test_weighted_proportional_distribution_and_bounds(engine: AllocationEngine):
    # Two torrents with different weights (speed and peers)
    # h1 has higher speed and peers → higher score → larger share
    torrents = [
        _t("h1", up_kib=800, peers=40),
        _t("h2", up_kib=200, peers=5),
    ]

    tracker_cap = 6 * 1024 * 1024
    limits = engine._calculate_limits_phase2(torrents)  # type: ignore[attr-defined]

    assert set(limits.keys()) == {"h1", "h2"}

    # Sum equals tracker cap
    assert sum(limits.values()) == tracker_cap

    # Respect per-torrent min and max bounds
    assert all(v >= 10 * 1024 for v in limits.values())
    assert all(v <= int(0.6 * tracker_cap) for v in limits.values())

    # h1 should receive more than h2
    assert limits["h1"] > limits["h2"]


def test_weighted_many_torrents_min_floor(engine: AllocationEngine):
    # Create many torrents to enforce min-per-torrent floor
    torrents = [_t(f"h{i}", up_kib=1, peers=0) for i in range(200)]

    limits = engine._calculate_limits_phase2(torrents)  # type: ignore[attr-defined]

    # All torrents should have at least 10KB/s
    assert all(v >= 10 * 1024 for v in limits.values())


def test_unlimited_tracker_sets_unlimited(engine: AllocationEngine):
    # Reconfigure matcher to treat trackerX as unlimited for this test
    engine.tracker_matcher.get_tracker_config.side_effect = lambda tracker_id: Mock(
        id=tracker_id,
        max_upload_speed=(-1 if tracker_id == "trackerX" else 1 * 1024 * 1024),
        priority=5,
        name="TrackerX",
    )

    torrents = [_t("h1", up_kib=100, peers=10)]
    limits = engine._calculate_limits_phase2(torrents)  # type: ignore[attr-defined]
    assert limits["h1"] == -1


def test_weighted_reduce_with_reducible(engine: AllocationEngine):
    # TrackerX cap is 6 MiB/s; one strong torrent plus many at floor should force reduce with reducible > 0
    strong = _t("hs", up_kib=900, peers=50)
    many = [_t(f"h{i}", up_kib=0, peers=0) for i in range(400)]
    torrents = [strong] + many

    limits = engine._calculate_limits_phase2(torrents)  # type: ignore[attr-defined]

    cap = 6 * 1024 * 1024
    # Strong torrent must receive less than the 60% cap due to accommodating floors
    assert limits["hs"] < int(0.6 * cap)
    # The many torrents stay at the 10 KiB/s floor
    floors = [limits[h.hash] for h in many]
    assert all(v == 10 * 1024 for v in floors)


def test_weighted_rounding_correction_negative(engine: AllocationEngine):
    # Force tracker cap to 1,000,001 and three equal torrents → rounding up sum > cap triggers reduce branch
    engine.tracker_matcher.get_tracker_config.side_effect = lambda tracker_id: Mock(
        id=tracker_id,
        max_upload_speed=1_000_001,
        priority=5,
        name="TrackerX",
    )
    ts = [_t("r1", 100, 10), _t("r2", 100, 10), _t("r3", 100, 10)]
    limits = engine._calculate_limits_phase2(ts)  # type: ignore[attr-defined]
    assert sum(limits.values()) == 1_000_001


def test_weighted_rounding_correction_positive(engine: AllocationEngine):
    # Force tracker cap to 1,000,000 and three equal torrents → rounding down sum < cap triggers add branch
    engine.tracker_matcher.get_tracker_config.side_effect = lambda tracker_id: Mock(
        id=tracker_id,
        max_upload_speed=1_000_000,
        priority=5,
        name="TrackerX",
    )
    ts = [_t("a", 100, 10), _t("b", 100, 10), _t("c", 100, 10)]
    limits = engine._calculate_limits_phase2(ts)  # type: ignore[attr-defined]
    assert sum(limits.values()) == 1_000_000
