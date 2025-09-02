"""Unit tests for Phase 2 selection of managed torrents"""

import time
from typing import List
from unittest.mock import AsyncMock, Mock

import pytest

from src.allocation import AllocationEngine
from src.config import QguardarrConfig


class DummyTorrent:
    def __init__(
        self,
        upspeed: int = 0,
        peers: int = 0,
        last_activity: int | None = None,
        hash_: str | None = None,
    ):
        self.upspeed = upspeed
        self.num_seeds = peers // 2
        self.num_leechs = peers - self.num_seeds
        self.last_activity = last_activity or int(time.time())
        self.hash = hash_ or f"h{upspeed}_{peers}_{self.last_activity}"
        self.tracker = "http://default/announce"

    @property
    def num_peers(self) -> int:
        return self.num_seeds + self.num_leechs


@pytest.fixture
def engine(test_config: QguardarrConfig):
    qbit = AsyncMock()
    matcher = Mock()
    matcher.match_tracker.return_value = "default"
    matcher.get_tracker_config.return_value = Mock(
        id="default", max_upload_speed=1048576, priority=1
    )
    rollback = AsyncMock()

    eng = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )

    # Constrain selection size for tests
    from src.allocation import ActivityScorer

    eng.activity_scorer = ActivityScorer(max_managed_torrents=15)
    return eng


def _build_mix(now: int) -> List[DummyTorrent]:
    torrents: List[DummyTorrent] = []
    # 10 high (upspeed > 10KB/s) → score 1.0
    for i in range(10):
        torrents.append(
            DummyTorrent(
                upspeed=20 * 1024, peers=0, last_activity=now, hash_=f"high{i}"
            )
        )
    # 10 medium (<6h recent + >5 peers) → ~0.6
    for i in range(10):
        torrents.append(
            DummyTorrent(
                upspeed=0, peers=8, last_activity=now - 2 * 3600, hash_=f"mid{i}"
            )
        )
    # 10 low (<24h and 0 peers) → ~0.2
    for i in range(10):
        torrents.append(
            DummyTorrent(
                upspeed=0, peers=0, last_activity=now - 12 * 3600, hash_=f"low{i}"
            )
        )
    return torrents


def test_select_respects_max_and_prefers_higher_scores(engine: AllocationEngine):
    now = int(time.time())
    all_torrents = _build_mix(now)

    selected = engine.select_torrents_for_management(all_torrents)

    # Must cap to 15
    assert len(selected) == 15

    selected_hashes = {t.hash for t in selected}
    # Expect all 10 highs included
    for i in range(10):
        assert f"high{i}" in selected_hashes
    # Expect remaining 5 from mediums, not lows
    low_selected = [h for h in selected_hashes if h.startswith("low")]
    assert len(low_selected) == 0


def test_selection_updates_stats(engine: AllocationEngine):
    now = int(time.time())
    all_torrents = _build_mix(now)

    _ = engine.select_torrents_for_management(all_torrents)
    stats = engine.get_detailed_stats()

    assert stats.get("managed_torrent_count") == 15
    sd = stats.get("score_distribution")
    assert isinstance(sd, dict)
    # There are 10 highs, 10 mediums, 10 low, 0 ignored
    assert sd.get("high") >= 10  # high-speed torrents guaranteed
    assert sd.get("medium") >= 10
    assert sd.get("low") >= 10
    assert sd.get("ignored") >= 0
