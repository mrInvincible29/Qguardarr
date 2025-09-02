"""Tests for extended tracker stats in Phase 3."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from src.allocation import AllocationEngine
from src.config import QguardarrConfig


def test_tracker_stats_include_effective_and_borrowed(test_config: QguardarrConfig):
    qbit = AsyncMock()
    matcher = Mock()

    # Provide a tracker config list with one tracker T1
    t1 = SimpleNamespace(
        id="T1", name="Tracker 1", max_upload_speed=4 * 1024 * 1024, priority=7
    )
    matcher.get_all_tracker_configs.return_value = [t1]
    matcher.get_tracker_config.side_effect = lambda tid: t1 if tid == "T1" else None

    rollback = AsyncMock()

    engine = AllocationEngine(
        config=test_config,
        qbit_client=qbit,
        tracker_matcher=matcher,
        rollback_manager=rollback,
    )

    # Simulate cache state: two torrents on T1
    engine.cache.add_torrent("h1", "T1", 1.0 * 1024 * 1024, 100000)
    engine.cache.add_torrent("h2", "T1", 0.5 * 1024 * 1024, 100000)

    # Simulate Phase 3 effective cap maintained by engine
    engine._last_effective_caps["T1"] = float(5 * 1024 * 1024)  # 5MB effective

    stats = engine.get_tracker_stats()

    assert "T1" in stats
    s = stats["T1"]
    # Existing fields
    assert s["name"] == "Tracker 1"
    assert s["configured_limit_mbps"] == 4.0
    assert s["active_torrents"] == 2
    # New fields
    assert s["priority"] == 7
    assert s["effective_cap_mbps"] == 5.0
    assert s["borrowed_mbps"] == 1.0
    # Efficiency = usage / effective
    assert 0.0 <= s["efficiency_percent"] <= 100.0
