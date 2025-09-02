"""Unit tests for Phase 3 soft borrowing allocator across trackers."""

from typing import List
from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


def _t(hash_: str, up_kib: int, peers: int, tracker: str) -> TorrentInfo:
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


@pytest.fixture
def engine(test_config: QguardarrConfig) -> AllocationEngine:
    qbit = AsyncMock()
    matcher = Mock()

    def match(url: str) -> str:
        if "trackerA" in url:
            return "A"
        if "trackerB" in url:
            return "B"
        if "trackerC" in url:
            return "C"
        return "default"

    matcher.match_tracker.side_effect = match

    def get_cfg(tracker_id: str):
        # Base caps: A=4MB/s, B=2MB/s, C=2MB/s by default
        caps = {"A": 4 * 1024 * 1024, "B": 2 * 1024 * 1024, "C": 2 * 1024 * 1024}
        prio = {"A": 10, "B": 10, "C": 5}
        return Mock(
            id=tracker_id,
            max_upload_speed=caps.get(tracker_id, 1_000_000),
            priority=prio.get(tracker_id, 1),
            name=tracker_id,
        )

    matcher.get_tracker_config.side_effect = get_cfg

    rollback = AsyncMock()

    eng = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )

    return eng


def test_soft_borrowing_basic(engine: AllocationEngine):
    # Tracker A unused (low usage), Tracker B needs more
    torrents: List[TorrentInfo] = [
        # A: very low usage (100 KiB/s)
        _t("a1", up_kib=100, peers=2, tracker="http://trackerA/announce"),
        # B: higher usage, two torrents
        _t("b1", up_kib=1500, peers=40, tracker="http://trackerB/announce"),
        _t("b2", up_kib=1000, peers=20, tracker="http://trackerB/announce"),
    ]

    # Compute Phase 3 limits
    limits = engine._calculate_limits_phase3(torrents)  # type: ignore[attr-defined]

    # Sum allocated for tracker B should exceed its base cap (2MB) but be capped by max_borrow_fraction (default 0.5 -> +1MB)
    b_total = sum(v for h, v in limits.items() if h.startswith("b"))
    assert b_total > 2 * 1024 * 1024
    assert b_total <= 3 * 1024 * 1024


def test_priority_weighting_for_borrowers(engine: AllocationEngine):
    # Large unused pool from A
    torrents: List[TorrentInfo] = [
        # Make pool small so max_borrow caps don't trigger for both
        _t("a1", up_kib=3500, peers=0, tracker="http://trackerA/announce"),
        # B and C both above their caps; B has higher priority (10) than C (5)
        _t("b1", up_kib=2500, peers=30, tracker="http://trackerB/announce"),
        _t("c1", up_kib=2500, peers=30, tracker="http://trackerC/announce"),
    ]

    limits = engine._calculate_limits_phase3(torrents)  # type: ignore[attr-defined]

    # Extract per-tracker totals
    b_total = sum(v for h, v in limits.items() if h.startswith("b"))
    c_total = sum(v for h, v in limits.items() if h.startswith("c"))

    # Base caps are equal (2MB). With borrowing and priority, B should receive more than C.
    assert b_total > c_total


def test_smoothing_reduces_churn(engine: AllocationEngine):
    # Scenario 1: Small pool allocated entirely to B
    torrents1: List[TorrentInfo] = [
        # A usage high -> small pool ~500 KiB
        _t("a1", up_kib=3584, peers=0, tracker="http://trackerA/announce"),  # 3.5 MiB
        _t("b1", up_kib=2200, peers=20, tracker="http://trackerB/announce"),
    ]

    limits1 = engine._calculate_limits_phase3(torrents1)  # type: ignore[attr-defined]
    b_total1 = sum(v for h, v in limits1.items() if h.startswith("b"))

    # Scenario 2: Tiny change in pool and usage for B (<10% total cap change)
    torrents2: List[TorrentInfo] = [
        _t(
            "a1", up_kib=3600, peers=0, tracker="http://trackerA/announce"
        ),  # +16 KiB used on A
        _t(
            "b1", up_kib=2216, peers=20, tracker="http://trackerB/announce"
        ),  # +16 KiB on B
    ]

    limits2 = engine._calculate_limits_phase3(torrents2)  # type: ignore[attr-defined]
    b_total2 = sum(v for h, v in limits2.items() if h.startswith("b"))

    # With smoothing and min_effective_delta=0.1 default, totals should remain equal (no churn)
    assert b_total2 == b_total1
