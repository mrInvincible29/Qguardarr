"""Unit tests for Phase 2 activity scoring"""

import time

import pytest

from src.allocation import AllocationEngine  # type: ignore


class DummyTorrent:
    """Minimal torrent-like object for scoring tests"""

    def __init__(
        self,
        upspeed: int = 0,
        num_seeds: int = 0,
        num_leechs: int = 0,
        last_activity: int | None = None,
    ):
        self.upspeed = upspeed
        self.num_seeds = num_seeds
        self.num_leechs = num_leechs
        self.last_activity = last_activity or int(time.time())

    @property
    def num_peers(self) -> int:
        return self.num_seeds + self.num_leechs


def _scorer():
    # Import lazily to avoid circulars during discovery
    from src.allocation import ActivityScorer  # type: ignore

    return ActivityScorer()


def test_high_upload_speed_scores_high():
    scorer = _scorer()
    # >10KB/s should immediately yield 1.0
    torrent = DummyTorrent(upspeed=20 * 1024, num_seeds=0, num_leechs=0)
    assert (
        pytest.approx(scorer.calculate_priority_score(torrent), rel=0, abs=1e-6) == 1.0
    )


def test_recent_activity_time_buckets():
    scorer = _scorer()
    now = int(time.time())

    # <1h → ~0.8 before peer boosts
    t1 = DummyTorrent(upspeed=0, num_seeds=0, num_leechs=0, last_activity=now - 10 * 60)
    assert scorer.calculate_priority_score(t1) == pytest.approx(0.8, abs=1e-6)

    # <6h → ~0.5
    t2 = DummyTorrent(
        upspeed=0, num_seeds=0, num_leechs=0, last_activity=now - 3 * 3600
    )
    assert scorer.calculate_priority_score(t2) == pytest.approx(0.5, abs=1e-6)

    # <24h → ~0.2
    t3 = DummyTorrent(
        upspeed=0, num_seeds=0, num_leechs=0, last_activity=now - 12 * 3600
    )
    assert scorer.calculate_priority_score(t3) == pytest.approx(0.2, abs=1e-6)

    # >24h → 0.0
    t4 = DummyTorrent(
        upspeed=0, num_seeds=0, num_leechs=0, last_activity=now - 48 * 3600
    )
    assert scorer.calculate_priority_score(t4) == 0.0


def test_peer_boosts_and_clamping():
    scorer = _scorer()
    now = int(time.time())

    # Base ~0.5 (<6h), +0.1 for >5 peers → 0.6
    t_mid = DummyTorrent(
        upspeed=0, num_seeds=3, num_leechs=4, last_activity=now - 2 * 3600
    )
    assert scorer.calculate_priority_score(t_mid) == pytest.approx(0.6, abs=1e-6)

    # Base ~0.8 (<1h), +0.3 for >20 peers → clamp to 1.0
    t_high = DummyTorrent(
        upspeed=0, num_seeds=15, num_leechs=10, last_activity=now - 10 * 60
    )
    assert scorer.calculate_priority_score(t_high) == 1.0
