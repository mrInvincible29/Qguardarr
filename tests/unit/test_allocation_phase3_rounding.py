"""Rounding correction tests for Phase 3 allocator (non-preview)."""

from unittest.mock import Mock

from src.allocation import AllocationEngine
from src.config import QguardarrConfig
from src.qbit_client import TorrentInfo


def _t(h: str, up_kib: int, peers: int, tr: str) -> TorrentInfo:
    return TorrentInfo(
        hash=h,
        name=h,
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
        tracker=tr,
    )


def test_phase3_rounding_reduce_branch(test_config: QguardarrConfig):
    # Two torrents with equal scores; choose cap not divisible by 2 to force rounding sum > cap
    test_config.global_settings.allocation_strategy = "soft"
    engine = AllocationEngine(
        config=test_config,
        qbit_client=Mock(),
        tracker_matcher=Mock(),
        rollback_manager=Mock(),
    )

    engine.tracker_matcher.match_tracker.return_value = "R"
    engine.tracker_matcher.get_tracker_config.return_value = Mock(
        id="R", name="R", max_upload_speed=1_000_001, priority=5
    )

    torrents = [_t("r1", 500, 10, "http://r"), _t("r2", 500, 10, "http://r")]
    limits = engine._calculate_limits_phase3(torrents)  # type: ignore[attr-defined]

    # Sum must equal cap after rounding correction
    assert sum(limits.values()) == 1_000_001


def test_phase3_rounding_add_branch_with_headroom(test_config: QguardarrConfig):
    # One heavy + two light torrents; heavy capped at 60%, small ones have headroom → add branch
    test_config.global_settings.allocation_strategy = "soft"
    engine = AllocationEngine(
        config=test_config,
        qbit_client=Mock(),
        tracker_matcher=Mock(),
        rollback_manager=Mock(),
    )

    cap = 1_000_000
    engine.tracker_matcher.match_tracker.return_value = "A"
    engine.tracker_matcher.get_tracker_config.return_value = Mock(
        id="A", name="A", max_upload_speed=cap, priority=5
    )

    # heavy gets capped at 60% (600_000), remainder should be distributed to light torrents via add-branch
    torrents = [
        _t("aH", 2000, 100, "http://a"),
        _t("a1", 0, 0, "http://a"),
        _t("a2", 0, 0, "http://a"),
    ]

    limits = engine._calculate_limits_phase3(torrents)  # type: ignore[attr-defined]

    assert limits["aH"] <= int(0.6 * cap)
    # Sum equals cap; light torrents received added headroom
    assert sum(limits.values()) == cap
    assert limits["a1"] > 10 * 1024 and limits["a2"] > 10 * 1024
